"""
ContextEngineer：上下文融合
- 并行调用 MemoryTool + RAGTool，单源失败降级
- Token 预算分配（system / history / memory / rag / query）
- 会话后异步持久化归档
"""
import asyncio
import logging

from app.core.config import settings
from app.tools.memory_factory import get_memory_tool
from app.tools.rag_factory import get_rag_tool
from app.utils.text import compute_importance, estimate_tokens

logger = logging.getLogger(__name__)


class ContextEngineer:
    async def build(self, user_id: str, query: str, rag_namespace: str | None) -> str:
        """构建增强后的 query：注入记忆与知识库上下文"""
        memory_ctx, rag_ctx = await asyncio.gather(
            self._recall_memory(user_id, query),
            self._recall_rag(rag_namespace, query),
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
                break
            budget -= cost
            parts.append(block)

        if not parts:
            return query
        return "\n\n".join(parts) + f"\n\n【客户问题】\n{query}"

    async def _recall_memory(self, user_id: str, query: str) -> str:
        """记忆检索，失败降级为空"""
        try:
            tool = get_memory_tool(user_id)
            result = await asyncio.to_thread(
                tool.run, {"action": "search", "query": query, "limit": 5}
            )
            if not result:
                return ""
            lines = [f"- {item}" for item in self._flatten(result)]
            return "【客户记忆】\n" + "\n".join(lines)
        except Exception as e:
            logger.warning(f"记忆检索失败（降级）: {e}")
            return ""

    async def _recall_rag(self, namespace: str | None, query: str) -> str:
        """知识库检索，失败降级为空"""
        if not namespace:
            return ""
        try:
            tool = get_rag_tool(namespace)
            result = await asyncio.to_thread(
                tool.run,
                {
                    "action": "search",
                    "query": query,
                    "limit": settings.rag_top_k,
                    "min_score": settings.rag_min_score,
                    "namespace": namespace,
                },
            )
            if not result:
                return ""
            text = str(result)
            # RAGTool 未命中时返回提示文本（🔍 开头），不注入上下文
            if text.startswith("🔍") or "未找到" in text:
                return ""
            lines = [f"- {item}" for item in self._flatten(result)]
            return "【知识库参考】\n" + "\n".join(lines)
        except Exception as e:
            logger.warning(f"RAG 检索失败（降级）: {e}")
            return ""

    @staticmethod
    def _flatten(result) -> list[str]:
        """将工具返回统一拍平为字符串列表"""
        if isinstance(result, list):
            return [str(r) for r in result]
        if isinstance(result, dict):
            items = result.get("results") or result.get("items") or result.get("memories") or []
            return [str(i) for i in items]
        return [str(result)]

    async def persist_after_run(
        self,
        user_id: str,
        session_id: str,
        query: str,
        agent_name: str,
        history: list[dict],
    ) -> None:
        """会话轮次结束后异步归档记忆（mirror 到 DB）"""
        try:
            metadata = {"agent_name": agent_name, "turn_count": len(history)}
            signals = {"turn_count": len(history)}
            importance = compute_importance(query, metadata, signals)
            if importance >= settings.memory_importance_threshold:
                tool = get_memory_tool(user_id)
                await asyncio.to_thread(
                    tool.run,
                    {
                        "action": "add",
                        "memory_type": "episodic",
                        "content": query,
                        "metadata": metadata,
                    },
                )
                from app.models.repositories import MemoryRepo

                await MemoryRepo.upsert_mirror(
                    user_id=user_id,
                    memory_uid=f"{session_id}:{abs(hash(query))}",
                    type_="episodic",
                    content=query,
                    importance=importance,
                    session_id=session_id,
                    metadata=metadata,
                )
        except Exception as e:
            logger.warning(f"记忆归档失败（不阻塞主流程）: {e}")
