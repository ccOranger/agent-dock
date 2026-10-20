"""LLM 客户端复用：进程级缓存 HelloAgentsLLM 实例。

hello_agents 的 HelloAgentsLLM 在 __init__ 中创建 OpenAI 客户端（含连接池），
而路由与业务 Agent 每轮都会新建实例；不复用时每轮都要重新建连，放大首字延迟。
同模型共用一个实例即可：客户端线程安全，模型/温度等参数由调用方逐次传入。
"""
import logging
import threading

from hello_agents import HelloAgentsLLM

logger = logging.getLogger(__name__)

_llm_cache: dict[str, HelloAgentsLLM] = {}
_cache_lock = threading.Lock()


def get_llm(model: str | None = None) -> HelloAgentsLLM:
    """按模型名获取进程级复用的 LLM 客户端（首次创建后常驻）。"""
    key = model or ""
    llm = _llm_cache.get(key)
    if llm is not None:
        return llm
    with _cache_lock:
        llm = _llm_cache.get(key)
        if llm is None:
            llm = HelloAgentsLLM(model=model)
            _llm_cache[key] = llm
            logger.info(f"创建并缓存 LLM 客户端: model={key or '(default)'}")
        return llm
