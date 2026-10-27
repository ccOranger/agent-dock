"""外部 IO 专用线程池。

RAG 检索、记忆检索、Qdrant 浏览、文档索引等调用底层是同步网络请求
（Qdrant / embedding / hello_agents 工具），一旦外部依赖挂起，工作线程会
一直阻塞且无法被安全取消。如果直接使用 asyncio.to_thread（事件循环默认
executor），这些线程被占满后，连 Agent 构建等无关的 to_thread 调用也会
排队 —— 单个外部依赖能把全站拖垮。

这里使用独立的线程池隔离影响面：外部依赖出问题时最多占满本池；
调用方再配合 asyncio.wait_for 做超时降级，就不会无限等待。
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# 并发上限：与 Qdrant/embedding 的打点匹配，避免“全部知识库”模式一次性打爆下游
EXTERNAL_IO_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="external-io")


async def run_in_pool(fn: Any, /, *args: Any) -> Any:
    """在外部 IO 专用线程池执行阻塞调用（只支持位置参数，不支持 kwargs）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXTERNAL_IO_POOL, fn, *args)
