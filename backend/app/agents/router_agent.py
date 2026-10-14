"""
RouterAgent：路由 Agent
- 独立于所有业务 Agent
- 只做意图识别与分发决策（支持多意图）
- 不加载任何业务工具
"""
import asyncio
import json
import logging

from hello_agents import HelloAgentsLLM
from hello_agents.agents import SimpleAgent

from app.agents.prompts.router import ROUTER_SYSTEM_PROMPT
from app.agents.registry import AGENT_REGISTRY, list_effective_agents
from app.core.config import settings
from app.core.tracing import SPAN_ROUTER, clip_payload

logger = logging.getLogger(__name__)

# 注入路由提示词的最近对话轮数与单条截断长度
HISTORY_TURNS = 6
HISTORY_CHARS = 200

# 解析失败/无有效意图时的兜底（与提示词约定一致）
FALLBACK_REASON = "解析失败"
FALLBACK_INTENT = {"agent_name": "knowledge_qa", "confidence": 0.3, "reason": FALLBACK_REASON}


class RouterAgent:
    def __init__(self):
        self.llm = HelloAgentsLLM(model=settings.router_model)
        # 默认提示词（仅代码注册表、无上下文）：保证无 DB 时仍可工作，也供测试直接读取
        self.agent = SimpleAgent(
            name="路由分发器",
            llm=self.llm,
            system_prompt=self._build_prompt(),
            tool_registry=None,  # 不加载任何工具
        )

    @staticmethod
    def _format_agent_list(agents: list[dict]) -> str:
        return "\n".join(f"- {a['name']}: {a['description']}" for a in agents)

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
        agent_list = "\n".join(
            f"- {name}: {cls.description}" for name, cls in AGENT_REGISTRY.items()
        )
        return ROUTER_SYSTEM_PROMPT.format(
            agent_list=agent_list,
            conversation="（无）",
            last_agent="（无）",
            max_intents=settings.router_max_intents,
        )

    async def build_prompt_async(self) -> str:
        """基于当前生效的 Agent（代码 + 启用中的动态）实时生成路由提示词。"""
        agents = await list_effective_agents()
        return self._render_prompt(agents, history=None, query="", last_route_to=None)

    def _render_prompt(
        self,
        agents: list[dict],
        history: list[dict] | None,
        query: str,
        last_route_to: str | None,
    ) -> str:
        return ROUTER_SYSTEM_PROMPT.format(
            agent_list=self._format_agent_list(agents),
            conversation=self._format_conversation(history, query),
            last_agent=last_route_to or "（无）",
            max_intents=settings.router_max_intents,
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
    def _parse_intents(raw: str, valid_names: set[str]) -> list[dict]:
        """归一化路由输出为意图列表。

        - 新格式：{"intents": [{agent_name, confidence, reason}, ...]}
        - 旧格式：{agent_name, confidence, reason} 单意图，同样接受
        - 未注册/重复的名称丢弃；全部无效或解析失败时回落 knowledge_qa 兜底
        """
        data = RouterAgent._extract_json(raw)
        if data is None:
            logger.warning(f"路由结果解析失败: {raw!r}")
            return [dict(FALLBACK_INTENT)]

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
            return [dict(FALLBACK_INTENT)]
        # 保持模型给出的主次顺序，不按置信度重排
        return intents

    async def route(
        self,
        query: str,
        history: list[dict] | None = None,
        last_route_to: str | None = None,
        collector=None,
    ) -> dict:
        """识别意图，返回 {"intents": [...], "agent_name"/"confidence"/"reason"(主意图，兼容旧调用方)}。"""
        # 每次路由实时拼入动态 Agent 清单，使后台新增的 Agent 无需改代码即可被路由到
        agents = await list_effective_agents()
        valid_names = {a["name"] for a in agents}
        system_prompt = self._render_prompt(agents, history, query, last_route_to)
        agent = SimpleAgent(
            name="路由分发器",
            llm=self.llm,
            system_prompt=system_prompt,
            tool_registry=None,
        )
        # SimpleAgent.run 为同步阻塞调用，放入线程池避免阻塞事件循环
        started_ms = collector.now_ms() if collector else 0
        raw = await asyncio.to_thread(agent.run, query)
        intents = self._parse_intents(raw, valid_names)
        primary = intents[0]

        if collector:
            # 路由原始输出必须留存：只能靠它区分“模型判错”还是“解析失败”
            fallback_hit = primary["reason"] == FALLBACK_REASON
            raw_text, raw_truncated = clip_payload(raw)
            collector.add(
                SPAN_ROUTER,
                settings.router_model,
                started_ms=started_ms,
                status="error" if fallback_hit else "ok",
                model=settings.router_model,
                candidate_agents=len(valid_names),
                intent_count=len(intents),
                fallback=fallback_hit or None,
                raw=raw_text,
                raw_truncated=raw_truncated or None,
            )
            collector.add_unique("llm_models", settings.router_model)

        return {
            "intents": intents,
            "agent_name": primary["agent_name"],
            "confidence": primary["confidence"],
            "reason": primary["reason"],
        }
