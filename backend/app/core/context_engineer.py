"""
ContextEngineer：上下文融合
- 并行调用 MemoryTool + RAGTool，单源失败降级
- Token 预算分配（system / history / memory / rag / query）
- 会话后异步持久化归档
"""
import asyncio
import logging
import re

from app.core.config import settings
from app.core.tracing import SPAN_MEMORY, SPAN_PERSIST, SPAN_RAG
from app.tools.memory_factory import get_memory_tool
from app.utils.text import compute_importance, estimate_tokens

logger = logging.getLogger(__name__)

# 用于从用户消息中提取信号的预编译正则
_ORDER_ID_RE = re.compile(r"(?:ORD|ORDER|TN)[\-_]?\d{6,}|订单号[：:]?\s*\d{6,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_TICKET_ID_RE = re.compile(r"(?:TK|TICKET)[\-_]?\d{4,}|工单号[：:]?\s*\d{4,}", re.IGNORECASE)
_EMOTIONAL_WORDS = ("投诉", "不满", "太差", "生气", "气死", "垃圾", "骗人", "退款", "举报", "差评", "敷衍")


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
        merged: list[dict] = []
        for ns in namespaces:
            started_ms = collector.now_ms() if collector else 0
            if collector:
                collector.add_unique("rag_namespaces", ns)
            try:
                # 纯向量检索（关闭 HyDE+MQE 高级检索）以保证对话延迟
                results = await svc.search(ns, query, top_k=top_k, advanced=False)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"RAG 检索失败（降级，ns={ns}）: {e}")
                if collector:
                    collector.add(
                        SPAN_RAG,
                        ns,
                        started_ms=started_ms,
                        status="error",
                        namespace=ns,
                        error=str(e)[:500],
                    )
                continue
            hits = len(results or [])
            if collector:
                collector.rag_chunk_count += hits
                collector.add(
                    SPAN_RAG,
                    ns,
                    started_ms=started_ms,
                    status="ok" if hits else "empty",
                    namespace=ns,
                    chunk_count=hits,
                    top_score=round(max((r.get("score", 0.0) for r in results or []), default=0.0), 4) or None,
                )
            for r in results or []:
                merged.append(r)
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

    async def persist_after_run(
        self,
        user_id: str,
        session_id: str,
        query: str,
        agent_name: str,
        history: list[dict],
        collector=None,
    ) -> None:
        """会话轮次结束后异步归档记忆（mirror 到 DB）"""
        started_ms = collector.now_ms() if collector else 0
        try:
            extra_meta, signals = self._extract_signals(query, history)
            metadata = {"agent_name": agent_name, "turn_count": len(history), **extra_meta}
            importance = compute_importance(query, metadata, signals)
            if importance < settings.memory_importance_threshold:
                # 未达写入阈值也要留痕，否则“为什么没记住”无从查起
                if collector:
                    collector.add(
                        SPAN_PERSIST,
                        "memory_add",
                        started_ms=started_ms,
                        status="skipped",
                        importance=round(importance, 3),
                        threshold=settings.memory_importance_threshold,
                    )
                return
            tool = get_memory_tool(user_id)
            # 使用已配置的记忆类型，避免写入不支持的层
            memory_type = "episodic"
            await asyncio.to_thread(
                tool.run,
                {
                    "action": "add",
                    "memory_type": memory_type,
                    "content": query,
                    "metadata": metadata,
                },
            )
            from app.models.repositories import MemoryRepo

            await MemoryRepo.upsert_mirror(
                user_id=user_id,
                memory_uid=f"{session_id}:{abs(hash(query))}",
                type_=memory_type if not extra_meta else "semantic",
                content=query,
                importance=importance,
                session_id=session_id,
                metadata=metadata,
            )
            if collector:
                collector.add(
                    SPAN_PERSIST,
                    "memory_add",
                    started_ms=started_ms,
                    status="ok",
                    memory_type=memory_type,
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
