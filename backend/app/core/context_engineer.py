"""
ContextEngineer：上下文融合
- 并行调用 MemoryTool + RAGTool，单源失败降级
- Token 预算分配（system / history / memory / rag / query）
- 会话后异步持久化归档
"""
import asyncio
import hashlib
import logging
import re
import time

from app.core.config import settings
from app.core.tracing import SPAN_MEMORY, SPAN_PERSIST, SPAN_RAG
from app.tools.memory_factory import get_memory_tool
from app.utils.text import compute_importance, estimate_tokens

logger = logging.getLogger(__name__)

# 后台计数/晋升任务强引用：防止 task 被 GC 后静默丢失
_BG_TASKS: set = set()

# working 镜像行过期清理节流：进程内至多 5 分钟扫一次，避免每轮对话都发 DELETE
_WORKING_PURGE_INTERVAL_SECONDS = 300
# None 表示本进程从未清理过；不能初始化为 0.0，否则开机不足 5 分钟（monotonic 很小）时首轮会被误判为刚扫过
_last_working_purge: float | None = None

# MemoryTool 检索结果行格式："N. [类型标签] 内容预览 (重要性: 0.55)"，预览超 80 字符带 ...
_HIT_LINE_RE = re.compile(r"^\s*\d+\.\s*\[[^\]]+\]\s*(.*?)\s*(?:\(重要性: [0-9.]+\)|\(importance: [0-9.]+\))\s*$")

# 用于从用户消息中提取信号的预编译正则
_ORDER_ID_RE = re.compile(r"(?:ORD|ORDER|TN)[\-_]?\d{6,}|订单号[：:]?\s*\d{6,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_TICKET_ID_RE = re.compile(r"(?:TK|TICKET)[\-_]?\d{4,}|工单号[：:]?\s*\d{4,}", re.IGNORECASE)
_EMOTIONAL_WORDS = ("投诉", "不满", "太差", "生气", "气死", "垃圾", "骗人", "退款", "举报", "差评", "敷衍")


def _maybe_purge_expired_working() -> None:
    """按 spec 8.1 的 30 分钟寿命清理未晋升的 working 镜像行，fire-and-forget 不阻塞主链路"""
    global _last_working_purge
    now = time.monotonic()
    if _last_working_purge is not None and now - _last_working_purge < _WORKING_PURGE_INTERVAL_SECONDS:
        return
    _last_working_purge = now

    async def _run() -> None:
        from app.models.repositories import MemoryRepo

        try:
            deleted = await MemoryRepo.purge_expired_working(settings.working_memory_ttl)
            if deleted:
                logger.info(f"清理过期 working 记忆镜像行 {deleted} 条")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"working 记忆清理失败（不阻塞）: {e}")

    task = asyncio.create_task(_run())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


class ContextEngineer:
    async def build(
        self,
        user_id: str,
        query: str,
        rag_namespaces: list[str] | None,
        collector=None,
    ) -> str:
        """构建增强后的 query：注入记忆与知识库上下文

        collector 不为空时，把“召回了多少、哪个知识库命中、是否因预算被丢弃”
        记为 span，否则这些只能靠日志推测。
        """
        memory_ctx, rag_ctx = await asyncio.gather(
            self._recall_memory(user_id, query, collector),
            self._recall_rag(rag_namespaces or [], query, collector),
        )

        # Token 预算裁剪：为 system + history 预留
        budget = settings.context_max_tokens - settings.context_system_reserve - settings.context_history_reserve
        parts: list[str] = []
        for block in (memory_ctx, rag_ctx):
            if not block:
                continue
            cost = estimate_tokens(block)
            if budget - cost < 0:
                logger.info("上下文预算不足，截断注入块")
                if collector:
                    # status=skipped 是“答非所问”的常见真因，必须在链路里可见
                    collector.add(
                        "context.budget",
                        "budget_drop",
                        status="skipped",
                        dropped_chars=len(block),
                        cost_tokens=cost,
                        remain_budget=budget,
                    )
                break
            budget -= cost
            parts.append(block)

        if not parts:
            return query
        return "\n\n".join(parts) + f"\n\n【客户问题】\n{query}"

    async def _recall_memory(self, user_id: str, query: str, collector=None) -> str:
        """记忆检索，失败降级为空"""
        started_ms = collector.now_ms() if collector else 0
        try:
            tool = get_memory_tool(user_id)
            result = await asyncio.to_thread(
                tool.run, {"action": "search", "query": query, "limit": 5}
            )
            items = self._flatten(result) if result else []
            if collector:
                collector.memory_hit_count = len(items)
                collector.add(
                    SPAN_MEMORY,
                    "memory_search",
                    started_ms=started_ms,
                    status="ok" if items else "empty",
                    hit_count=len(items),
                )
            if items:
                # 命中即计数（spec 8.6：access_count 达阈自动晋升），后台执行不阻塞对话主链路
                prefixes = self._hit_prefixes(result)
                if prefixes:
                    task = asyncio.create_task(self._count_and_promote(user_id, prefixes))
                    _BG_TASKS.add(task)
                    task.add_done_callback(_BG_TASKS.discard)
            if not items:
                return ""
            lines = [f"- {item}" for item in items]
            return "【客户记忆】\n" + "\n".join(lines)
        except Exception as e:
            logger.warning(f"记忆检索失败（降级）: {e}")
            if collector:
                collector.add(
                    SPAN_MEMORY,
                    "memory_search",
                    started_ms=started_ms,
                    status="error",
                    error=str(e)[:500],
                )
            return ""

    async def _recall_rag(self, namespaces: list[str], query: str, collector=None) -> str:
        """知识库检索（跨多个命名空间，按相似度归并），失败降级为空"""
        namespaces = [n for n in namespaces if n]
        if not namespaces:
            return ""
        from app.services.rag_service import RagService

        svc = RagService()
        top_k = settings.rag_top_k

        if collector:
            for ns in namespaces:
                collector.add_unique("rag_namespaces", ns)

        async def _search_one(ns: str):
            started_ms = collector.now_ms() if collector else 0
            try:
                # 纯向量检索（关闭 HyDE+MQE 高级检索）以保证对话延迟
                results = await svc.search(ns, query, top_k=top_k, advanced=False)
                return ns, list(results or []), None, started_ms
            except Exception as e:  # noqa: BLE001
                return ns, [], e, started_ms

        # 多库并发检索：命名空间互不依赖，串行会让延迟随库数线性叠加。
        # 信号量限制并发度，避免“全部知识库”模式下一次性打爆 Qdrant/embedding。
        semaphore = asyncio.Semaphore(4)

        async def _search_limited(ns: str):
            async with semaphore:
                return await _search_one(ns)

        outcomes = await asyncio.gather(*(_search_limited(ns) for ns in namespaces))

        merged: list[dict] = []
        for ns, results, error, started_ms in outcomes:
            if error is not None:
                logger.warning(f"RAG 检索失败（降级，ns={ns}）: {error}")
                if collector:
                    collector.add(
                        SPAN_RAG,
                        ns,
                        started_ms=started_ms,
                        status="error",
                        namespace=ns,
                        error=str(error)[:500],
                    )
                continue
            hits = len(results)
            if collector:
                collector.rag_chunk_count += hits
                collector.add(
                    SPAN_RAG,
                    ns,
                    started_ms=started_ms,
                    status="ok" if hits else "empty",
                    namespace=ns,
                    chunk_count=hits,
                    top_score=round(max((r.get("score", 0.0) for r in results), default=0.0), 4) or None,
                )
            merged.extend(results)
        if not merged:
            return ""
        merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        lines: list[str] = []
        for r in merged[:top_k]:
            content = (r.get("content") or "").strip()
            if not content:
                continue
            src = r.get("source_name") or ""
            prefix = f"[{src}] " if src else ""
            lines.append(f"- {prefix}{content}")
        if not lines:
            return ""
        return "【知识库参考】\n" + "\n".join(lines)

    @staticmethod
    def _flatten(result) -> list[str]:
        """将工具返回统一拍平为字符串列表"""
        if isinstance(result, list):
            return [str(r) for r in result]
        if isinstance(result, dict):
            items = result.get("results") or result.get("items") or result.get("memories") or []
            return [str(i) for i in items]
        return [str(result)]

    @staticmethod
    def _extract_signals(query: str, history: list[dict]) -> tuple[dict, dict]:
        """从用户消息中提取结构化信号，返回 (metadata_extras, signals)。"""
        metadata: dict = {}
        signals: dict = {"turn_count": len(history)}

        # 订单号
        m = _ORDER_ID_RE.search(query)
        if m:
            metadata["order_id"] = m.group()
        # 工单号
        m = _TICKET_ID_RE.search(query)
        if m:
            metadata["ticket_id"] = m.group()
        # 手机号
        m = _PHONE_RE.search(query)
        if m:
            metadata["phone"] = m.group()
        # 情感强度（出现情感关键词越多，强度越高，上限 1.0）
        hits = sum(1 for w in _EMOTIONAL_WORDS if w in query)
        if hits:
            signals["sentiment_intensity"] = min(hits / 3.0, 1.0)
        # 重复提及：检查同一用户消息是否重复出现关键实体
        if len(history) >= 3:
            prev_user_msgs = [h["content"] for h in history[-6:] if h.get("role") == "user"]
            for key_val in metadata.values():
                if isinstance(key_val, str) and any(key_val in pm for pm in prev_user_msgs[:-1]):
                    signals["repeat_mention"] = True
                    break
        return metadata, signals

    @staticmethod
    def _hit_prefixes(result) -> list[str]:
        """从召回结果解析内容前缀（可能被截断为 80 字符），用于镜像表匹配计数"""
        prefixes: list[str] = []
        for line in str(result).splitlines():
            m = _HIT_LINE_RE.match(line)
            if not m:
                continue
            text = m.group(1)
            if text.endswith("..."):
                text = text[:-3]
            if text:
                prefixes.append(text)
        return prefixes

    async def _count_and_promote(self, user_id: str, prefixes: list[str]) -> None:
        """访问计数：working 行被反复召回达阈 → 晋升长期记忆（spec 8.6）"""
        from app.models.repositories import MemoryRepo

        try:
            touched = await MemoryRepo.touch_access(user_id, prefixes)
            promoted = [
                r
                for r in touched
                if r.type == "working" and r.access_count >= settings.memory_access_threshold
            ]
            await self._persist_longterm(user_id, promoted)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"记忆访问计数失败（不阻塞）: {e}")

    @staticmethod
    async def _persist_longterm(user_id: str, rows) -> None:
        """把晋升行补写进 hello_agents 长期层，并同步镜像行类型（只升不降）"""
        if not rows:
            return
        from app.models.repositories import MemoryRepo

        tool = get_memory_tool(user_id)
        for row in rows:
            await asyncio.to_thread(
                tool.run,
                {
                    "action": "add",
                    "memory_type": "episodic",
                    "content": row.content,
                    "importance": row.importance,
                    "metadata": row.metadata_ or {},
                },
            )
            await MemoryRepo.upsert_mirror(
                user_id=row.user_id,
                memory_uid=row.memory_uid,
                type_="episodic",
                content=row.content,
                importance=row.importance,
                session_id=row.session_id,
                source_message_id=getattr(row, "source_message_id", None),
                metadata=row.metadata_ or {},
            )

    async def promote_on_session_close(self, user_id: str, session_id: str) -> None:
        """会话结束时全量晋升：该会话未晋升的 working 记忆转 episodic（spec 8.6）"""
        from app.models.repositories import MemoryRepo

        try:
            rows = await MemoryRepo.promote_working_by_session(user_id, session_id)
            await self._persist_longterm(user_id, rows)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"会话结束记忆晋升失败（不阻塞）: {e}")

    def schedule_after_run(self, **kwargs) -> None:
        """把本轮记忆归档放入后台任务：done 事件与连接释放不再等待两次 embedding。

        归档异常在 persist_after_run 内部已降级记日志；_BG_TASKS 持有强引用防止任务被 GC。
        """
        task = asyncio.create_task(self.persist_after_run(**kwargs))
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)

    async def persist_after_run(
        self,
        user_id: str,
        session_id: str,
        query: str,
        agent_name: str,
        history: list[dict],
        message_id: str | None = None,
        collector=None,
    ) -> None:
        """会话轮次结束后异步归档记忆：每轮写 working 层，达标晋升长期（spec 8.1/8.6）"""
        started_ms = collector.now_ms() if collector else 0
        try:
            extra_meta, signals = self._extract_signals(query, history)
            metadata = {"agent_name": agent_name, "turn_count": len(history), **extra_meta}
            importance = compute_importance(query, metadata, signals)
            tool = get_memory_tool(user_id)
            # spec 8.1：每轮都写 working 层（进程内、带 TTL，参与召回最高优先），不再因未达阈而丢弃
            await asyncio.to_thread(
                tool.run,
                {
                    "action": "add",
                    "memory_type": "working",
                    "content": query,
                    "importance": importance,
                    "metadata": metadata,
                },
            )
            # 晋升判定（spec 8.6）：重要性达阈 或 关键实体（订单/工单/投诉关键词）；
            # access_count 达阈晋升在 _count_and_promote；hello_agents 未启用 semantic 层，
            # 长期写入统一用 episodic，镜像行带实体时标 semantic 仅作展示语义
            promote = (
                importance >= settings.memory_importance_threshold
                or "order_id" in extra_meta
                or "ticket_id" in extra_meta
                or bool(signals.get("sentiment_intensity"))
            )
            mirror_type = "working"
            if promote:
                await asyncio.to_thread(
                    tool.run,
                    {
                        "action": "add",
                        "memory_type": "episodic",
                        "content": query,
                        "importance": importance,
                        "metadata": metadata,
                    },
                )
                mirror_type = "semantic" if extra_meta else "episodic"
            from app.models.repositories import MemoryRepo

            await MemoryRepo.upsert_mirror(
                user_id=user_id,
                memory_uid=f"{session_id}:{hashlib.sha1(query.encode('utf-8')).hexdigest()[:16]}",
                type_=mirror_type,
                content=query,
                importance=importance,
                session_id=session_id,
                # 溯源到当轮用户消息，避免与 messages 表成为两份无关联的同源文本
                source_message_id=message_id,
                metadata=metadata,
            )
            # 顺手触发过期 working 行清理（内部节流，绝大多数轮次直接返回）
            _maybe_purge_expired_working()
            if collector:
                collector.add(
                    SPAN_PERSIST,
                    "memory_add",
                    started_ms=started_ms,
                    status="ok",
                    memory_type=mirror_type,
                    promoted=promote,
                    importance=round(importance, 3),
                    signals=sorted(signals.keys()),
                )
        except Exception as e:
            logger.warning(f"记忆归档失败（不阻塞主流程）: {e}")
            if collector:
                collector.add(
                    SPAN_PERSIST,
                    "memory_add",
                    started_ms=started_ms,
                    status="error",
                    error=str(e)[:500],
                )


async def drain_background_tasks(timeout: float = 5.0) -> None:
    """应用关闭前排空后台归档任务：避免正在写入的记忆被事件循环取消而整轮丢失。"""
    pending = set(_BG_TASKS)
    if not pending:
        return
    _, not_done = await asyncio.wait(pending, timeout=timeout)
    if not_done:
        logger.warning(
            f"后台记忆归档仍有 {len(not_done)} 个任务未在 {timeout}s 内完成，已放弃等待"
        )
