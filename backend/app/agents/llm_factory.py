"""LLM 客户端复用：进程级缓存端口对象。

为什么缓存：``HelloAgentsLLM.__init__`` 会创建 OpenAI 客户端（含连接池），
而路由与业务 Agent 每轮都会新建实例；不复用时每轮都要重新建连，放大首字延迟。
同模型共用一个实例即可：客户端线程安全，模型/温度等参数由调用方逐次传入。

P1 起本模块不再直接依赖 hello_agents——具体实现由 ``core.ports.adapters`` 提供，
调用方只认 ``LLMPort``。
"""
import logging
import threading

from app.core.ports.adapters.hello_agents import create_llm
from app.core.ports.protocols import LLMPort

logger = logging.getLogger(__name__)

_llm_cache: dict[str, LLMPort] = {}
_cache_lock = threading.Lock()


def get_llm(model: str | None = None) -> LLMPort:
    """按模型名获取进程级复用的 LLM 客户端（首次创建后常驻）。"""
    key = model or ""
    llm = _llm_cache.get(key)
    if llm is not None:
        return llm
    with _cache_lock:
        llm = _llm_cache.get(key)
        if llm is None:
            llm = create_llm(model)
            _llm_cache[key] = llm
            logger.info(f"创建并缓存 LLM 客户端: model={key or '(default)'}")
        return llm
