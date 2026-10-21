"""P1 超时兜底回归测试：MCP 发现负缓存、RAG 检索硬超时。

不依赖真实 DB/Qdrant/子进程：
- 负缓存：mock MCPTool 构造为阻塞，验证超时后第二次调用直接跳过；
- RAG 超时：mock RAGTool pipeline 为阻塞，验证 search 在超时后抛 TimeoutError。
"""
import time

import pytest
from hello_agents.tools import ToolRegistry

from app.services import rag_service as rs
from app.tools import mcp_loader


def test_mcp_discovery_failure_is_negatively_cached(monkeypatch):
    """发现超时后，同一配置指纹在负缓存期内直接跳过，不再重复建线程/子进程。"""
    calls = {"count": 0}

    class _SlowMCPTool:
        def __init__(self, name, server_command):
            calls["count"] += 1
            self.name = name
            time.sleep(0.5)

    monkeypatch.setattr(mcp_loader, "MCPTool", _SlowMCPTool)
    cfg = {"name": "bad_server", "config": {"transport": "stdio", "command": "", "args": []}}
    timeout = 0.1
    key = mcp_loader._cache_key("bad_server", mcp_loader._build_mcp_server_source(cfg), timeout)
    mcp_loader._failure_cache.pop(key, None)
    try:
        registry = ToolRegistry()
        first = mcp_loader._register_one(registry, "bad_server", cfg, timeout)
        assert first == []
        assert calls["count"] == 1

        # 第二次：命中负缓存，应立即返回且不再构造 MCPTool（不再新建发现线程）
        started = time.monotonic()
        second = mcp_loader._register_one(registry, "bad_server", cfg, timeout)
        elapsed = time.monotonic() - started
        assert second == []
        assert calls["count"] == 1
        assert elapsed < 0.05
    finally:
        mcp_loader._failure_cache.pop(key, None)


async def test_rag_search_times_out_instead_of_hanging(monkeypatch):
    """Qdrant/embedding 挂起时，检索必须在超时后抛 TimeoutError（上层降级为空）。"""

    class _HangingRagTool:
        def _get_pipeline(self, namespace):
            return {"search": self._hang}

        @staticmethod
        def _hang(**kwargs):
            time.sleep(1.0)
            return []

    monkeypatch.setattr(rs, "get_rag_tool", lambda ns: _HangingRagTool())
    monkeypatch.setattr(rs.settings, "rag_timeout", 0.1, raising=False)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await rs.RagService().search("cs_test", "q", advanced=False)
    # 不应等到底层 1s 阻塞结束
    assert time.monotonic() - started < 0.6
