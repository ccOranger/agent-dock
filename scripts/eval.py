"""评测与回归执行器：打真实 HTTP 链路，产出指标与基线对比报告

为什么必须打 HTTP 而不是直接调 Agent（docs/productization-plan.md 3.4 的硬约束）：
只有走 ``POST /api/v1/chat/stream``，鉴权、场景解析、限流、固定话术、路由、
链路追踪、用量落库才会一起被检验——直接 import Orchestrator 跑出来的分数
不代表交付时那一套链路。

RAG 与 token 不在 SSE 里（流上没有 rag 事件，token 只在服务端估算），
因此本执行器是"HTTP 采集 + 服务端链路回读"的混合模式：
对话事实从流里读，``rag_chunk_count`` / ``rag_namespaces`` / token 按 trace_id
回读 ``request_traces`` 与 ``chat_events``。链路受采样影响时该项记为"不可观测"，
不进指标分母（口径见 docs/evaluation.md）。

打分/聚合/报告的全部逻辑在 ``app.services.eval_runner``（纯函数，可单测），
本文件只负责采集与落地。

用法（backend 目录）：
    .\\.venv\\Scripts\\python.exe ..\\scripts\\eval.py --tag w4-baseline
    .\\.venv\\Scripts\\python.exe ..\\scripts\\eval.py --scene customer_service --baseline latest
    .\\.venv\\Scripts\\python.exe ..\\scripts\\eval.py --limit 5 --dry-run   # 只校验用例集不发消息

退出码：0 全通过且无退化 / 1 有失败用例 / 2 相对基线退化 / 3 链路不可达或用例集不合法
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend"))
# settings 的 env_file 是相对路径：不在 backend 下执行时主动切过去，避免读到默认配置
if not Path(".env").is_file() and (_ROOT / "backend" / ".env").is_file():
    os.chdir(_ROOT / "backend")

import httpx
from app.core.config import settings
from app.core.constants import API_PREFIX
from app.core.database import SessionLocal, engine
from app.models.entities import ChatEvent, EvalRun, RequestTrace
from app.schemas.eval import EvalObservation, EvalRunSummary
from app.services import eval_runner
from sqlalchemy import select

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REGRESSED = 2
EXIT_UNREACHABLE = 3

# 路由兜底的两种来源：调度层的场景池兜底意图、路由层解析失败兜底
_FALLBACK_REASONS = ("scene_fallback", "解析失败")


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_db_time(value: datetime | None) -> str:
    """eval_runs 存 UTC，报告里统一换算到本地时钟。

    不转换会出现“本次 09:27 / 基线 01:27”这种看着像 8 小时前的基线，
    验收单上会被当成拿错了对照。
    """
    if value is None:
        return ""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone().strftime("%Y-%m-%d %H:%M:%S")


class StreamResult:
    """单轮 SSE 采集的中间结果（换 EvalObservation 前先在流里攒着）"""

    def __init__(self, case_id: str) -> None:
        self.obs = EvalObservation(case_id=case_id)
        self.started = time.perf_counter()
        self.first_text_at: float | None = None

    def finish(self) -> EvalObservation:
        obs = self.obs
        obs.total_ms = int((time.perf_counter() - self.started) * 1000)
        if self.first_text_at is not None:
            obs.first_token_ms = int((self.first_text_at - self.started) * 1000)
        obs.answer_chars = len(obs.answer or "")
        return obs


def _headers(args: argparse.Namespace, token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if args.api_key:
        headers["X-API-Key"] = args.api_key
    if args.publish_key:
        headers["X-Publish-Key"] = args.publish_key
    if args.integration_code:
        headers["X-Integration-Code"] = args.integration_code
    return headers


async def _login(client: httpx.AsyncClient, base: str, args: argparse.Namespace) -> str | None:
    """客户登录：default 这类未开放匿名的场景需要它（或 --api-key / --publish-key）。"""
    if not args.customer:
        return None
    resp = await client.post(
        f"{base}{API_PREFIX}/auth/customer/login",
        json={"username": args.customer, "password": args.password},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"客户登录失败 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json().get("data") or {}
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"登录响应里没有 access_token：{resp.text[:200]}")
    return token


def _apply_route_event(obs: EvalObservation, data: dict) -> None:
    """route 事件：意图清单、置信度、兜底标记。"""
    intents = data.get("intents") or []
    names = [i.get("agent_name") for i in intents if isinstance(i, dict) and i.get("agent_name")]
    obs.intents = names or ([data["agent_name"]] if data.get("agent_name") else [])
    conf = data.get("confidence")
    obs.confidence = float(conf) if isinstance(conf, (int, float)) else None
    # 兜底有两种来源：路由层解析失败/低置信兜底，与场景池兜底意图
    obs.fallback = any(
        isinstance(i, dict)
        and (i.get("is_fallback") or i.get("reason") in _FALLBACK_REASONS)
        for i in intents
    ) or data.get("reason") in _FALLBACK_REASONS


def _apply_event(res: StreamResult, name: str, data: dict) -> None:
    obs = res.obs
    if name == "session":
        obs.session_id = str(data.get("session_id") or "")
    elif name == "route":
        _apply_route_event(obs, data)
    elif name == "tool_call":
        tool = str(data.get("name") or "")
        if tool and tool not in obs.tools:
            obs.tools.append(tool)
    elif name == "tool_result":
        if data.get("error"):
            obs.tool_error_count += 1
    elif name == "handover":
        obs.handover = True
        obs.handover_reason = str(data.get("reason") or "")
    elif name == "text":
        piece = (data.get("content") or "") if isinstance(data, dict) else str(data)
        if piece and res.first_text_at is None:
            res.first_text_at = time.perf_counter()
        obs.answer += piece
    elif name == "done":
        # done 里的 trace_id 是服务端 collector 用的那个，比响应头更权威：回读全靠它
        tid = str(data.get("trace_id") or "")
        if tid:
            obs.trace_id = tid
    elif name == "error":
        obs.executed = False
        obs.error_code = data.get("code")
        obs.exec_error = str(data.get("message") or f"SSE error {data.get('code')}")


async def _one_round(
    client: httpx.AsyncClient,
    base: str,
    headers: dict[str, str],
    *,
    case_id: str,
    scene_code: str,
    message: str,
    visitor_id: str,
    session_id: str | None,
) -> StreamResult:
    """发一轮消息并读完 SSE 流。异常与超时都收敛成 executed=False，不让整批评测中断。"""
    res = StreamResult(case_id)
    payload = {
        "message": message,
        "scene_code": scene_code,
        "visitor_id": visitor_id,
        "session_id": session_id,
        "stream": True,
    }
    try:
        async with client.stream(
            "POST", f"{base}{API_PREFIX}/chat/stream", json=payload, headers=headers
        ) as resp:
            res.obs.http_status = resp.status_code
            res.obs.trace_id = resp.headers.get("X-Trace-Id", "") or ""
            if resp.status_code != 200:
                raw = (await resp.aread()).decode("utf-8", "replace")
                res.obs.executed = False
                res.obs.exec_error = f"HTTP {resp.status_code}: {raw[:160]}"
                try:
                    detail = (json.loads(raw) or {}).get("detail")
                    if isinstance(detail, dict):
                        res.obs.error_code = detail.get("code")
                    elif isinstance(detail, str):
                        res.obs.exec_error = detail[:160]
                except json.JSONDecodeError:
                    pass
                return res
            event = ""
            buffer: list[str] = []

            def flush() -> None:
                if not event or not buffer:
                    return
                try:
                    data = json.loads("\n".join(buffer))
                except json.JSONDecodeError:
                    data = {}
                if isinstance(data, dict):
                    _apply_event(res, event, data)

            async for line in resp.aiter_lines():
                if not line:
                    flush()
                    event, buffer = "", []
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    buffer.append(line[5:].strip())
            flush()
    except httpx.HTTPError as exc:
        res.obs.executed = False
        res.obs.exec_error = f"{type(exc).__name__}: {exc}"[:200]
    return res


async def run_case(
    client: httpx.AsyncClient,
    base: str,
    args: argparse.Namespace,
    headers: dict[str, str],
    case,
    stamp: str,
) -> EvalObservation:
    """一条用例 = 同一会话里"铺垫轮 + 正式提问"，只判定最后一轮。

    visitor key 每次运行都换（``ev-<stamp>-<case_id>``）：访客 key 相同会复用会话与
    跨轮记忆，第二次跑同一用例集就会带着第一次的上下文，"跑两次指标一致"立刻失效。
    """
    visitor_id = f"ev-{stamp}-{case.case_id}"[:36]
    session_id: str | None = None
    for msg in case.setup:
        setup_res = await _one_round(
            client,
            base,
            headers,
            case_id=f"{case.case_id}(setup)",
            scene_code=case.scene,
            message=msg,
            visitor_id=visitor_id,
            session_id=session_id,
        )
        session_id = setup_res.obs.session_id or session_id
    res = await _one_round(
        client,
        base,
        headers,
        case_id=case.case_id,
        scene_code=case.scene,
        message=case.query,
        visitor_id=visitor_id,
        session_id=session_id,
    )
    return res.finish()


async def readback(observations: list[EvalObservation]) -> dict[str, dict]:
    """按 trace_id 回读链路事实：RAG 片段/命名空间/工具与 token。

    request_traces 受采样丢弃（answered 轮按 TRACE_SAMPLE_RATE），chat_events 全量；
    两张表分别回读，缺哪张就是哪一项不可观测。
    """
    ids = [o.trace_id for o in observations if o.trace_id]
    facts: dict[str, dict] = {}
    if not ids:
        return facts
    async with SessionLocal() as db:
        for chunk in (ids[i : i + 50] for i in range(0, len(ids), 50)):
            rows = (
                await db.execute(
                    select(
                        RequestTrace.trace_id,
                        RequestTrace.rag_chunk_count,
                        RequestTrace.rag_namespaces,
                        RequestTrace.tool_names,
                        RequestTrace.tool_error_count,
                        RequestTrace.router_fallback,
                        RequestTrace.outcome,
                    ).where(RequestTrace.trace_id.in_(chunk))
                )
            ).all()
            for row in rows:
                facts[row[0]] = {
                    "rag_chunks": row[1],
                    "rag_namespaces": list(row[2] or []),
                    "trace_tools": list(row[3] or []),
                    "tool_error_count": row[4],
                    "router_fallback": row[5],
                    "outcome": row[6],
                }
            events = (
                await db.execute(
                    select(ChatEvent.trace_id, ChatEvent.total_tokens).where(
                        ChatEvent.trace_id.in_(chunk)
                    )
                )
            ).all()
            for trace_id, tokens in events:
                facts.setdefault(trace_id, {})["tokens"] = tokens
    return facts


def apply_facts(observations: list[EvalObservation], facts: dict[str, dict]) -> None:
    for obs in observations:
        item = facts.get(obs.trace_id) or {}
        obs.rag_chunks = item.get("rag_chunks")
        obs.rag_namespaces = list(item.get("rag_namespaces") or [])
        obs.tokens = item.get("tokens")
        if item.get("tool_error_count"):
            obs.tool_error_count = item["tool_error_count"]
        # 链路里的工具名比流里更全（含 MCP 展开后的名字），实测为准
        if item.get("trace_tools"):
            obs.tools = list(dict.fromkeys(obs.tools + item["trace_tools"]))
        if item.get("router_fallback"):
            obs.fallback = True


async def load_baseline(args: argparse.Namespace, fixture_hash: str) -> tuple[dict, str, str, str, str]:
    """返回 (metrics, 指纹, 标签, 时间, run_id)。没给 --baseline 时返回空指标。"""
    if not args.baseline:
        return {}, "", "", "", ""
    spec = args.baseline.strip()
    # 带后缀或已存在的文件先当 sidecar 报告读：本地对比不需要连着数据库
    candidate = Path(spec)
    if spec.endswith(".json") or candidate.is_file():
        if not candidate.is_file():
            raise FileNotFoundError(f"基线文件不存在：{candidate}")
        blob = json.loads(candidate.read_text(encoding="utf-8"))
        summary = blob.get("summary", blob)
        return (
            summary.get("metrics", {}),
            summary.get("fixture_hash", ""),
            summary.get("run_tag", ""),
            summary.get("started_at", ""),
            str(summary.get("run_id") or ""),
        )
    async with SessionLocal() as db:
        if spec == "latest":
            stmt = (
                select(EvalRun)
                .where(EvalRun.fixture_hash == fixture_hash)
                .order_by(EvalRun.started_at.desc())
                .limit(1)
            )
            if args.scene:
                stmt = stmt.where(EvalRun.scene_code == args.scene)
        else:
            try:
                run_uuid = uuid.UUID(spec)
            except ValueError as exc:
                raise RuntimeError(
                    f"--baseline 只能是 latest / eval_runs 的 run_id / sidecar json 路径，收到：{spec}"
                ) from exc
            stmt = select(EvalRun).where(EvalRun.id == run_uuid)
        run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        if spec == "latest":
            raise RuntimeError(
                f"没有同用例集（指纹 {fixture_hash[:16]}）且场景匹配的历史运行可作基线；"
                "先跑一次不带 --baseline 的运行，或确认上一次运行有没有写库（--no-db 不留痕）"
            )
        raise RuntimeError(f"基线运行不存在（eval_runs 里没有 {spec}）；先跑一次不带 --baseline 的运行")
    return (
        run.metrics or {},
        run.fixture_hash,
        run.run_tag,
        _fmt_db_time(run.started_at),
        str(run.id),
    )


async def persist_run(
    summary: EvalRunSummary, metrics: dict, baseline_run_id: str, report_path: Path, started_at: datetime
) -> str:
    """写 eval_runs：一次运行一行，"上一次跑是多少"从此有据可查。"""
    row = EvalRun(
        run_tag=summary.run_tag,
        scene_code=summary.scene or "all",
        base_url=summary.base_url,
        model_name=summary.model_name,
        fixture_hash=summary.fixture_hash,
        case_count=summary.metrics.total,
        passed_count=summary.metrics.passed,
        metrics=metrics,
        detail=summary.as_detail_rows(),
        report_path=str(report_path),
        baseline_run_id=uuid.UUID(baseline_run_id) if baseline_run_id else None,
        duration_ms=summary.duration_ms,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )
    async with SessionLocal() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return str(row.id)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="智能客服评测与回归执行器")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="被测服务地址")
    parser.add_argument("--fixture-dir", default=None, help="用例集目录，默认 backend/tests/fixtures/eval")
    parser.add_argument("--scene", default=None, help="只跑某个场景的用例")
    parser.add_argument("--tag", default=None, help="只跑带某个标签的用例")
    parser.add_argument("--run-tag", default="", dest="run_tag", help="本次运行的标注（报告标题/基线检索）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条，冒烟用")
    parser.add_argument("--concurrency", type=int, default=2, help="并发数：模型有速率限制，别无脑拉高")
    parser.add_argument("--timeout", type=float, default=120.0, help="单轮读超时（秒）")
    parser.add_argument("--api-key", default=None, help="X-API-Key：接入方 API Key")
    parser.add_argument("--publish-key", default=None, help="X-Publish-Key：场景公开标识")
    parser.add_argument("--integration-code", default=None, help="X-Integration-Code：接入方公开标识")
    parser.add_argument("--customer", default=None, help="客户账号（未开放匿名的场景需要）")
    parser.add_argument("--password", default=None, help="客户密码")
    parser.add_argument("--model-name", default=None, help="报告里标注的模型名，默认取服务端配置")
    parser.add_argument("--no-trace", action="store_true", help="不回读链路（RAG/token 指标记为不可观测）")
    parser.add_argument("--no-db", action="store_true", help="不写 eval_runs（临时试跑）")
    parser.add_argument("--baseline", default=None, help="latest | eval_runs 的 run_id | sidecar json 路径")
    parser.add_argument("--out-dir", default=str(_ROOT / "reports"), help="报告输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只校验并列出用例，不发请求")
    args = parser.parse_args(argv)
    # 场景过滤只影响选哪些用例：请求里仍按每条用例自己的 scene 发送
    args.scene_filter = args.scene
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cases = eval_runner.load_cases(
            Path(args.fixture_dir) if args.fixture_dir else None,
            scene=args.scene_filter,
            tag=args.tag,
        )
    except eval_runner.FixtureError as exc:
        print(f"[FAIL] 用例集不合法：{exc}")
        return EXIT_UNREACHABLE
    # 指纹在 --limit 之后算：冒烟跑的子集有自己一份指纹，与全量基线天然不可比，
    # 不会发生“拿 3 条的分数去对 39 条的基线”这种看起来很正常的误判
    if args.limit:
        cases = cases[: args.limit]
    fixture_hash = eval_runner.fingerprint(cases)
    print(f">> 用例 {len(cases)} 条（指纹 {fixture_hash}），场景={args.scene_filter or '全部'}")
    if args.dry_run:
        for case in cases:
            print(f"   {case.case_id} [{case.scene}] {case.query[:40]}")
        return EXIT_OK

    try:
        code = asyncio.run(_run(args, cases, fixture_hash))
    except KeyboardInterrupt:
        print("\n[ABORT] 已中断，本次结果不入库")
        code = EXIT_UNREACHABLE
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}")
        code = EXIT_UNREACHABLE
    return code


async def _run(args: argparse.Namespace, cases: list, fixture_hash: str) -> int:
    base = args.base_url.rstrip("/")
    stamp = datetime.now().strftime("%m%d%H%M%S")
    started_wall = time.perf_counter()
    started_dt = datetime.now(UTC)
    started_at = started_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(args.timeout, connect=10.0),
        limits=httpx.Limits(max_connections=max(args.concurrency, 1) + 2),
    ) as client:
        try:
            token = await _login(client, base, args)
        except httpx.HTTPError as exc:
            print(f"[FAIL] 链路不可达：{base}（{exc}）")
            return EXIT_UNREACHABLE
        except RuntimeError as exc:
            print(f"[FAIL] {exc}")
            return EXIT_UNREACHABLE
        headers = _headers(args, token)
        try:
            ping = await client.get(f"{base}/health")
            if ping.status_code >= 400:
                print(f"[WARN] 健康检查返回 {ping.status_code}，继续")
        except httpx.HTTPError as exc:
            print(f"[FAIL] 链路不可达：{base}（{exc}）")
            return EXIT_UNREACHABLE

        sem = asyncio.Semaphore(max(1, args.concurrency))

        async def guarded(case):
            async with sem:
                return case.case_id, await run_case(client, base, args, headers, case, stamp)

        print(f">> 并发 {args.concurrency}，开始采集…")
        gathered = await asyncio.gather(*(guarded(c) for c in cases))

    obs_by_id = dict(gathered)
    observations = [obs_by_id[c.case_id] for c in cases]
    unreachable = sum(1 for o in observations if o.http_status is None)
    if unreachable == len(observations):
        print(f"[FAIL] 全部用例都没连上：{base}{API_PREFIX}/chat/stream")
        return EXIT_UNREACHABLE

    if not args.no_trace:
        apply_facts(observations, await readback(observations))
        print(f">> 链路回读完成：{sum(1 for o in observations if o.rag_chunks is not None)} 条有 trace 事实")

    results = [eval_runner.score_case(c, obs_by_id[c.case_id]) for c in cases]
    metrics = eval_runner.aggregate(results)
    try:
        base_metrics, base_hash, base_tag, base_at, base_run_id = await load_baseline(args, fixture_hash)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[FAIL] {exc}")
        await engine.dispose()
        return EXIT_UNREACHABLE
    diffs = (
        eval_runner.diff_metrics(
            metrics.as_dict(), base_metrics, current_hash=fixture_hash, baseline_hash=base_hash
        )
        if base_metrics
        else []
    )
    duration_ms = int((time.perf_counter() - started_wall) * 1000)
    summary = EvalRunSummary(
        run_tag=args.run_tag or f"{args.scene_filter or 'all'}-{stamp}",
        scene=args.scene_filter or "",
        base_url=base,
        model_name=args.model_name or settings.llm_model_id,
        fixture_dir=str(args.fixture_dir or eval_runner.DEFAULT_FIXTURE_DIR),
        fixture_hash=fixture_hash,
        concurrency=args.concurrency,
        trace_readback=not args.no_trace,
        started_at=started_at,
        finished_at=_now_iso(),
        duration_ms=duration_ms,
        metrics=metrics,
        results=results,
        diffs=diffs,
        baseline_hash=base_hash,
        baseline_tag=base_tag,
        baseline_at=base_at,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = out_dir / f"eval-{ts}.md"
    sidecar_path = out_dir / f"eval-{ts}.json"
    report_path.write_text(eval_runner.render_report(summary), encoding="utf-8")
    sidecar_path.write_text(
        json.dumps(
            {"summary": summary.model_dump(), "cases": [c.case_id for c in cases]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run_id = ""
    if not args.no_db:
        run_id = await persist_run(summary, metrics.as_dict(), base_run_id, report_path, started_dt)
    await engine.dispose()

    print(f"\n[SUMMARY] 通过 {metrics.passed}/{metrics.total}，失败 {metrics.failed}，传输故障 {metrics.errors}")
    print(
        f"          意图准确率 {metrics.intent_accuracy.value if metrics.intent_accuracy.value is not None else '—'}，"
        f"工具命中率 {metrics.tool_hit_rate.value if metrics.tool_hit_rate.value is not None else '—'}，"
        f"RAG 命中率 {metrics.rag_hit_rate.value if metrics.rag_hit_rate.value is not None else '—'}"
    )
    for line in (
        f"          报告：{report_path}",
        f"          明细：{sidecar_path}" + (f"，eval_runs run_id={run_id}" if run_id else "（未入库）"),
    ):
        print(line)
    if metrics.failed:
        print("          失败用例：" + "、".join(f.case_id for f in summary.failures()))
    regressed = [d.label for d in diffs if d.regressed]
    if regressed:
        print(f"[REGRESSION] 相对基线退化：{'、'.join(regressed)}")
        return EXIT_REGRESSED
    return EXIT_FAILED if metrics.failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
