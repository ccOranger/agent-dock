"""MemoryService：包装 MemoryTool 的读写检索"""
import asyncio

from app.tools.memory_factory import get_memory_tool


class MemoryService:
    async def search(self, user_id: str, query: str, limit: int = 10):
        tool = get_memory_tool(user_id)
        return await asyncio.to_thread(
            tool.run, {"action": "search", "query": query, "limit": limit}
        )

    async def add(self, user_id: str, type_: str, content: str, metadata: dict):
        tool = get_memory_tool(user_id)
        return await asyncio.to_thread(
            tool.run,
            {"action": "add", "memory_type": type_, "content": content, "metadata": metadata},
        )

    async def list_memories(self, user_id: str, type_: str | None = None, limit: int = 50):
        tool = get_memory_tool(user_id)
        args = {"action": "list", "limit": limit}
        if type_:
            args["memory_type"] = type_
        return await asyncio.to_thread(tool.run, args)

    async def update(self, user_id: str, memory_uid: str, content: str | None, importance: float | None):
        tool = get_memory_tool(user_id)
        args = {"action": "update", "memory_uid": memory_uid}
        if content is not None:
            args["content"] = content
        if importance is not None:
            args["importance"] = importance
        return await asyncio.to_thread(tool.run, args)

    async def forget(self, user_id: str, memory_uid: str):
        tool = get_memory_tool(user_id)
        return await asyncio.to_thread(tool.run, {"action": "forget", "memory_uid": memory_uid})
