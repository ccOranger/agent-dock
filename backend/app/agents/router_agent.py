"""
RouterAgent：路由 Agent
- 独立于所有业务 Agent
- 只做意图识别与分发决策（支持多意图）
- 不加载任何业务工具
"""
import asyncio
import json
import logging

from app.agents.llm_factory import get_llm
from app.agents.prompts.router import GLOBAL_SCENE_RULES, ROUTER_SYSTEM_PROMPT, render_system_prompt
from app.agents.registry import AGENT_REGISTRY, list_agents, list_effective_agents
from app.core.config import settings
from app.core.ports.adapters.hello_agents import SimpleAgent
from app.core.tracing import SPAN_ROUTER, clip_payload
from app.schemas.scene import DEFAULT_ASSISTANT_NAME, SceneRouterProfile

logger = logging.getLogger(__name__)

# 注入路由提示词的最近对话轮数与单条截断长度
HISTORY_TURNS = 6
HISTORY_CHARS = 200

# 单个 Agent 注入路由提示词的线索条数上限（控制提示词体积，Agent 增多也不线性膨胀）
ROUTING_KEYWORDS_LIMIT = 12
ROUTING_EXAMPLES_LIMIT = 3

# 解析失败/无有效意图时的兜底（与提示词约定一致）
FALLBACK_REASON = "解析失败"
# 兜底意图的置信度：与 prompts/router.py 里“confidence 填 0.3”的字面约定保持一致。
# 它故意低于 ROUTER_CONFIDENCE_THRESHOLD，所以用 is_fallback 标记区分“兜底”与“模型真实低置信”，
# 由调度层根据 ROUTER_FALLBACK_ANSWER 决定是兜底应答还是转人工。
FALLBACK_CONFIDENCE = 0.3
# 兜底 Agent 的优先候选：存在且启用时首选；被后台禁用则依次降级到生效清单首项
# 业务 Agent 均改为后台动态配置后，兜底到中立的默认助手；动态 Agent 由场景自行绑定
FALLBACK_CANDIDATES = (DEFAULT_ASSISTANT_NAME,)
# 生效清单为空（极端：全部 Agent 被禁用）时的兜底名
FALLBACK_AGENT_DEFAULT = DEFAULT_ASSISTANT_NAME

# 路由分发器自身标识（它不在 AGENT_REGISTRY / agents 表中，由代码定义，后台只读）
ROUTER_AGENT_NAME = "路由分发器"
ROUTER_ENV_KEYS = {
    "model": "ROUTER_MODEL",
    "confidence_threshold": "ROUTER_CONFIDENCE_THRESHOLD",
    "max_intents": "ROUTER_MAX_INTENTS",
    "fallback_answer": "ROUTER_FALLBACK_ANSWER",
}


# 链路里每个意图记录的命中关键词上限（避免链路 span 体积随关键词清单线性撑大）
EVIDENCE_KEYWORDS_LIMIT = 8


def fallback_intent(agent_name: str) -> dict:
    """兜底意图：Agent 名由当前生效清单派生，不再硬编码。

    带上 `is_fallback` 标记：它的 confidence 是提示词约定的固定低值、不是模型的真实判断，
    不能当成“低置信”直接转人工。
    """
    return {
        "agent_name": agent_name,
        "confidence": FALLBACK_CONFIDENCE,
        "reason": FALLBACK_REASON,
        "is_fallback": True,
    }


def match_routing_clues(query: str, agent: dict | None) -> list[str]:
    """把目标 Agent 配置的路由关键词逐个拿去原句里做子串匹配，返回命中的词。

    注意：路由判定本身是 LLM 的语义判断，不看分词、也不走关键词匹配。这里只是**事后回显**
    “这句话里确实出现了哪几个已配置的关键词”，供链路详情看出「哪个词把请求抢给了这个 Agent」
    或「配了线索却一个都没中」。典型问法是整句，字面命中概率极低，因此不参与匹配。
    """
    text = (query or "").strip().lower()
    if not text or not agent:
        return []
    hits: list[str] = []
    for kw in agent.get("routing_keywords") or []:
        word = str(kw).strip()
        if word and word.lower() in text and word not in hits:
            hits.append(word)
    return hits[:EVIDENCE_KEYWORDS_LIMIT]


def annotate_intent_evidence(intents: list[dict], agents: list[dict], query: str) -> list[dict]:
    """给每个意图补上「意图依据」（就地修改并返回同一列表）。

    `clue_basis` 三态，后台据此给不同文案：
    - keyword_hit  命中的词里有该 Agent 配置的关键词
    - keyword_miss 该 Agent 配了关键词但这句里一个都没出现（靠语义/适用范围判的）
    - no_keywords  该 Agent 压根没配关键词（路由提示词里只有一行适用范围）
    """
    by_name = {str(a.get("name") or ""): a for a in agents}
    for intent in intents:
        agent = by_name.get(intent.get("agent_name") or "")
        hits = match_routing_clues(query, agent)
        intent["matched_keywords"] = hits
        if hits:
            intent["clue_basis"] = "keyword_hit"
        elif (agent or {}).get("routing_keywords"):
            intent["clue_basis"] = "keyword_miss"
        else:
            intent["clue_basis"] = "no_keywords"
    return intents


class RouterAgent:
    def __init__(self):
        # 进程级复用 LLM 客户端：避免每次请求都新建 OpenAI 连接池
        self.llm = get_llm(settings.router_model)
        # 默认提示词（仅代码注册表、无上下文）：保证无 DB 时仍可工作，也供测试直接读取
        self.agent = SimpleAgent(
            name=ROUTER_AGENT_NAME,
            llm=self.llm.raw,
            system_prompt=self._build_prompt(),
            tool_registry=None,  # 不加载任何工具
        )

    @staticmethod
    def _format_agent_list(agents: list[dict]) -> str:
        """每个 Agent 渲染成信息块：名称 + 适用范围（描述）+ 关键词 + 典型问法。

        线索缺失时降级为一行描述，不阻塞路由；路由提示词不再硬编码任何具名优先规则，
        因此后台新建的动态 Agent 只要填好线索就能被路由到。
        """
        blocks: list[str] = []
        for a in agents:
            name = str(a.get("name") or "").strip()
            lines = [f"- {name}：{a.get('description') or name}"]
            keywords = [str(k).strip() for k in (a.get("routing_keywords") or []) if str(k).strip()]
            if keywords:
                lines.append(f"  关键词：{'、'.join(keywords[:ROUTING_KEYWORDS_LIMIT])}")
            examples = [str(e).strip() for e in (a.get("routing_examples") or []) if str(e).strip()]
            if examples:
                lines.append(f"  典型问法：{' / '.join(examples[:ROUTING_EXAMPLES_LIMIT])}")
            blocks.append("\n".join(lines))
        return "\n".join(blocks)

    @staticmethod
    def resolve_fallback_agent(agents: list[dict]) -> str:
        """从生效清单算出兜底 Agent（避免提示词/代码指向已被禁用的 Agent）。"""
        names = [str(a.get("name") or "") for a in agents if a.get("name")]
        for candidate in FALLBACK_CANDIDATES:
            if candidate in names:
                return candidate
        return names[0] if names else FALLBACK_AGENT_DEFAULT

    @staticmethod
    def _format_conversation(history: list[dict] | None, query: str) -> str:
        """把最近几轮对话压成简短文本供路由判定追问；末尾与当前 query 重复的消息会被剔除。"""
        if not history:
            return "（无）"
        turns = list(history)
        # chat_service 会先把当前消息写入 history，这里去掉避免与 query 重复
        last = turns[-1] if turns else None
        if last and last.get("role") == "user" and str(last.get("content") or "").strip() == query.strip():
            turns = turns[:-1]
        lines: list[str] = []
        for item in turns[-HISTORY_TURNS:]:
            content = str(item.get("content") or "").strip().replace("\n", " ")
            if not content:
                continue
            role = "客户" if item.get("role") == "user" else "客服"
            lines.append(f"- {role}: {content[:HISTORY_CHARS]}")
        return "\n".join(lines) or "（无）"

    def _build_prompt(self) -> str:
        """仅基于代码注册表构建（兼容旧测试；动态列表请用 build_prompt_async）。"""
        agents = list_agents()
        return render_system_prompt(
            agent_list=self._format_agent_list(agents),
            max_intents=settings.router_max_intents,
            fallback_agent=self.resolve_fallback_agent(agents),
        )

    async def build_prompt_async(self) -> str:
        """基于当前生效的 Agent（代码 + 启用中的动态）实时生成路由提示词。"""
        agents = await list_effective_agents()
        return self._render_prompt(agents, history=None, query="", last_route_to=None)

    @staticmethod
    def _render_prompt(
        agents: list[dict],
        history: list[dict] | None,
        query: str,
        last_route_to: str | None,
        *,
        scene_block: str = "",
        max_intents: int | None = None,
        fallback_agent: str | None = None,
    ) -> str:
        """渲染本轮系统提示词。

        场景档案（SceneRouterProfile）与全局视图共用本方法：候选清单、场景约束、
        意图上限与兜底名都可由调用方显式传入，避免两套渲染逻辑漂移。
        """
        return render_system_prompt(
            agent_list=RouterAgent._format_agent_list(agents),
            scene_rules=scene_block or GLOBAL_SCENE_RULES,
            conversation=RouterAgent._format_conversation(history, query),
            last_agent=last_route_to or "（无）",
            max_intents=max_intents if max_intents is not None else settings.router_max_intents,
            fallback_agent=fallback_agent or RouterAgent.resolve_fallback_agent(agents),
        )

    @staticmethod
    def _extract_json(raw: str) -> dict | None:
        """从模型输出中取出 JSON 对象（容忍 ```json 代码块与前后杂字）。"""
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _parse_intents(
        raw: str, valid_names: set[str], fallback_agent: str = FALLBACK_AGENT_DEFAULT
    ) -> list[dict]:
        """归一化路由输出为意图列表。

        - 新格式：{"intents": [{agent_name, confidence, reason}, ...]}
        - 旧格式：{agent_name, confidence, reason} 单意图，同样接受
        - 未注册/重复的名称丢弃；全部无效或解析失败时回落到 fallback_agent（由生效清单派生）
        """
        data = RouterAgent._extract_json(raw)
        if data is None:
            logger.warning(f"路由结果解析失败: {raw!r}")
            return [fallback_intent(fallback_agent)]

        raw_intents = data.get("intents")
        if not isinstance(raw_intents, list):
            # 兼容仍输出旧单意图格式的模型
            raw_intents = [data] if data.get("agent_name") else []

        intents: list[dict] = []
        seen: set[str] = set()
        for item in raw_intents:
            if not isinstance(item, dict):
                continue
            name = str(item.get("agent_name") or "").strip()
            if not name or name in seen:
                continue
            if valid_names and name not in valid_names:
                logger.warning(f"路由结果含未注册的 Agent: {name}（已忽略）")
                continue
            seen.add(name)
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            intents.append(
                {
                    "agent_name": name,
                    "confidence": confidence,
                    "reason": str(item.get("reason") or ""),
                }
            )

        if not intents:
            logger.warning(f"路由结果无有效意图: {raw!r}")
            return [fallback_intent(fallback_agent)]
        # 保持模型给出的主次顺序，不按置信度重排
        return intents

    async def route(
        self,
        query: str,
        history: list[dict] | None = None,
        last_route_to: str | None = None,
        collector=None,
        profile: SceneRouterProfile | None = None,
    ) -> dict:
        """识别意图，返回 {"intents": [...], "agent_name"/"confidence"/"reason"(主意图，兼容旧调用方)}。

        - profile 非空：候选、场景约束、模型、意图上限与兜底全部取自场景路由档案，
          路由只能在本场景池内选择；
        - profile 为空：保留旧的全局视图行为（候选=全部生效 Agent），仅供兼容调用。
        """
        if profile is not None:
            agents = [dict(c) for c in profile.candidates if c.get("name")]
            valid_names = {a["name"] for a in agents}
            fallback_agent = profile.fallback_agent or self.resolve_fallback_agent(agents)
            max_intents = profile.max_intents
            model = profile.model
            system_prompt = self._render_prompt(
                agents,
                history,
                query,
                last_route_to,
                scene_block=profile.scene_block,
                max_intents=max_intents,
                fallback_agent=fallback_agent,
            )
        else:
            # 每次路由实时拼入动态 Agent 清单，使后台新增的 Agent 无需改代码即可被路由到
            agents = await list_effective_agents()
            valid_names = {a["name"] for a in agents}
            fallback_agent = self.resolve_fallback_agent(agents)
            max_intents = settings.router_max_intents
            model = settings.router_model
            system_prompt = self._render_prompt(agents, history, query, last_route_to)
        agent = SimpleAgent(
            name=ROUTER_AGENT_NAME,
            # 场景可覆盖路由模型：不同模型共用一个进程级客户端缓存；.raw 为框架互操作点
            llm=(self.llm if model == settings.router_model else get_llm(model)).raw,
            system_prompt=system_prompt,
            tool_registry=None,
        )
        # SimpleAgent.run 为同步阻塞调用，放入线程池避免阻塞事件循环
        started_ms = collector.now_ms() if collector else 0
        raw = await asyncio.to_thread(agent.run, query)
        if collector:
            # 路由是一次真实 LLM 调用：系统提示词 + 用户句算输入，JSON 意图算输出。
            # _format_conversation 已把 query 嵌进 system_prompt，run(query) 又发了一次，
            # 这里按实际下发内容计（宁多算不漏算）。
            collector.record_prompt(f"{system_prompt}\n{query}")
            collector.record_completion(raw)
        intents = self._parse_intents(raw, valid_names, fallback_agent)
        # 回显「意图依据」：这句话命中了目标 Agent 的哪几个关键词（本地子串匹配，仅供链路参考）
        annotate_intent_evidence(intents, agents, query)
        primary = intents[0]

        if collector:
            # 路由原始输出必须留存：只能靠它区分“模型判错”还是“解析失败”
            fallback_hit = bool(primary.get("is_fallback")) or primary["reason"] == FALLBACK_REASON
            raw_text, raw_truncated = clip_payload(raw)
            span_extra = {}
            if profile is not None:
                # 场景身份与配置指纹一起入链路：同一 prompt_hash 的兜底率才有可比性
                span_extra = {
                    "scene_code": profile.scene_code,
                    "candidate_count": len(valid_names),
                    "prompt_hash": profile.prompt_hash,
                    "router_mode": profile.mode,
                }
            collector.add(
                SPAN_ROUTER,
                model,
                started_ms=started_ms,
                status="error" if fallback_hit else "ok",
                model=model,
                candidate_agents=len(valid_names),
                intent_count=len(intents),
                fallback=fallback_hit or None,
                raw=raw_text,
                raw_truncated=raw_truncated or None,
                **span_extra,
            )
            collector.add_unique("llm_models", model)
            # 拍平字段：路由耗时 / 配置指纹 / 兜底标记，后台统计直接聚合这些列
            collector.router_ms = collector.now_ms() - started_ms
            collector.router_fallback = collector.router_fallback or fallback_hit
            if profile is not None:
                collector.prompt_hash = profile.prompt_hash

        return {
            "intents": intents,
            "agent_name": primary["agent_name"],
            "confidence": primary["confidence"],
            "reason": primary["reason"],
            # 本轮生效的兜底 Agent（由清单派生）：供调度层在意图 Agent 不可用时回落
            "fallback_agent": fallback_agent,
            # 场景档案信息透传给调度层与后台试运行
            "mode": profile.mode if profile is not None else "global",
            "fallback_text": profile.fallback_text if profile is not None else None,
            "prompt_hash": profile.prompt_hash if profile is not None else None,
        }


# ===== 后台只读自省 =====
# 路由分发器由代码 + .env 定义（不在 agents 表里），后台只提供查看，不提供任何修改入口。


def _clue_level(keywords: list[str], examples: list[str]) -> str:
    """线索完整度：决定该 Agent 在路由提示词里能拿到多少表达机会。"""
    if keywords and examples:
        return "full"
    if keywords:
        return "keywords_only"
    if examples:
        return "examples_only"
    return "description_only"


def _candidate_brief(agent: dict) -> dict:
    """单个候选 Agent 的路由视角摘要：完整线索 + 实际注入提示词的部分（受条数上限截断）。

    保留 id：场景配置的 fallback_agent_id 要靠它在候选池内匹配；
    提示词渲染只读 name/description/routing_*，多余字段不会进入提示词。
    """
    name = str(agent.get("name") or "")
    keywords = [str(k).strip() for k in (agent.get("routing_keywords") or []) if str(k).strip()]
    examples = [str(e).strip() for e in (agent.get("routing_examples") or []) if str(e).strip()]
    return {
        "id": str(agent.get("id") or ""),
        "name": name,
        "description": agent.get("description") or name,
        "source": "code" if name in AGENT_REGISTRY else "dynamic",
        "routing_keywords": keywords,
        "routing_examples": examples,
        "keywords_total": len(keywords),
        "examples_total": len(examples),
        "keywords_injected": keywords[:ROUTING_KEYWORDS_LIMIT],
        "examples_injected": examples[:ROUTING_EXAMPLES_LIMIT],
        "clue_level": _clue_level(keywords, examples),
    }


def describe_router_agent() -> dict:
    """路由分发器的静态档案：职责、生效参数、提示词模板与占位符说明。"""
    return {
        "name": ROUTER_AGENT_NAME,
        "agent_type": "router",
        "source": "code",
        "editable": False,
        "responsibility": "只做意图识别与分发（支持一条消息多意图），不加载业务工具、不直接回答客户",
        "model": settings.router_model,
        "max_intents": settings.router_max_intents,
        "confidence_threshold": settings.router_confidence_threshold,
        "confidence_threshold_effect": (
            "主意图置信度低于此值时整轮转人工；仅次要意图不达标则只丢弃该分支。"
            "例外：路由判定“不贴合任何 Agent”而给出的兜底意图不受此阈值影响（见下方兜底应答）"
        ),
        "fallback_candidates": list(FALLBACK_CANDIDATES),
        "fallback_agent_default": FALLBACK_AGENT_DEFAULT,
        "fallback_confidence": FALLBACK_CONFIDENCE,
        "fallback_answer": settings.router_fallback_answer,
        "fallback_answer_effect": (
            "兜底意图（置信度固定为约定低值）交给兜底 Agent 应答，不转人工"
            if settings.router_fallback_answer
            else "兜底意图仍按低置信处理 → 整轮转人工"
        ),
        "history_turns": HISTORY_TURNS,
        "history_chars": HISTORY_CHARS,
        "keywords_limit": ROUTING_KEYWORDS_LIMIT,
        "examples_limit": ROUTING_EXAMPLES_LIMIT,
        "system_prompt_template": ROUTER_SYSTEM_PROMPT,
        "placeholders": [
            {
                "name": "{scene_rules}",
                "desc": "本场景的候选边界与补充规则（全局视图下为“不限定场景”）",
            },
            {
                "name": "{agent_list}",
                "desc": "生效 Agent 清单（名称 + 适用范围 + 关键词 + 典型问法），每轮按库中现状实时渲染",
            },
            {
                "name": "{conversation}",
                "desc": f"最近对话（最多 {HISTORY_TURNS} 轮，单条截断 {HISTORY_CHARS} 字）",
            },
            {"name": "{last_agent}", "desc": "上一轮承接的业务 Agent，用于追问续接"},
            {"name": "{max_intents}", "desc": "单条消息最多意图数（多意图串行执行上限）"},
            {
                "name": "{fallback_agent}",
                "desc": "都不贴合时的兜底 Agent（由当前生效清单派生，非硬编码），并按约定置信度输出",
            },
        ],
        "definition_files": {
            "agent": "backend/app/agents/router_agent.py",
            "prompt": "backend/app/agents/prompts/router.py",
        },
        "env_keys": dict(ROUTER_ENV_KEYS),
    }


async def render_router_prompt() -> dict:
    """按当前生效清单渲染出“这一轮会真正发给模型的提示词”（不访问 LLM，无对话上下文）。"""
    agents = await list_effective_agents()
    prompt = RouterAgent._render_prompt(agents, history=None, query="", last_route_to=None)
    fallback_agent = RouterAgent.resolve_fallback_agent(agents)
    return {
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "candidate_count": len(agents),
        "fallback_agent": fallback_agent,
        # 兜底是否命中优先候选（否则说明 default_assistant 被禁用或不在本场景池内，已降级到清单首项）
        "fallback_from_candidates": fallback_agent in FALLBACK_CANDIDATES,
        "candidates": [_candidate_brief(a) for a in agents],
    }

