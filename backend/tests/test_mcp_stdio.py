"""stdio MCP 配置运行时解析（方案 A）：跨环境可移植 + 旧库绝对路径兼容回退。

纯函数测试，不建连、不起子进程。核心保证：
- DB 里 command 留空 / 存相对路径也能拉起（用运行进程解释器 + backend 根重定位）；
- 旧库写死的异机绝对路径（Windows 盘符等）被自动纠正到当前 backend 根；
- 远程 sse/http 配置不受影响。
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

from app.tools.mcp_loader import (
    _BACKEND_ROOT,
    _build_mcp_server_source,
    _resolve_stdio_arg,
    _resolve_stdio_command,
)


def _stdio(command, args):
    return _build_mcp_server_source({"config": {"transport": "stdio", "command": command, "args": args}})


# ---------------- 解释器解析 ----------------
def test_empty_command_falls_back_to_running_interpreter():
    assert _resolve_stdio_command("") == sys.executable
    assert _resolve_stdio_command(None) == sys.executable


def test_bare_command_name_preserved_for_path_lookup():
    assert _resolve_stdio_command("python") == "python"
    assert _resolve_stdio_command("docker") == "docker"


def test_windows_npx_shim_resolved_to_cmd():
    # DB 存跨平台裸名 npx；Windows 上 npx 实为 npx.cmd 包装，加载时应补 .cmd
    if os.name != "nt":
        pytest.skip("仅 Windows 验证 npx -> npx.cmd")
    if not shutil.which("npx.cmd"):
        pytest.skip("本机 PATH 无 npx.cmd")
    assert _resolve_stdio_command("npx") == "npx.cmd"


def test_bare_name_without_cmd_suffix_kept_as_is():
    # 无对应 .cmd 包装的裸命令不应被强加后缀
    if os.name != "nt":
        pytest.skip("仅 Windows 验证 PATH 无 .cmd 时原样保留")
    if shutil.which("python.cmd"):
        pytest.skip("本机存在 python.cmd，不适合该断言")
    assert _resolve_stdio_command("python") == "python"


def test_existing_absolute_command_kept_missing_falls_back():
    assert _resolve_stdio_command(sys.executable) == sys.executable
    assert _resolve_stdio_command("/nonexistent/venv/bin/python") == sys.executable


# ---------------- 脚本参数解析 ----------------
def test_relative_script_resolved_under_backend_root():
    resolved = _resolve_stdio_arg("mcp_servers/logistics_server.py")
    assert Path(resolved) == (_BACKEND_ROOT / "mcp_servers" / "logistics_server.py").resolve()


def test_legacy_foreign_absolute_path_relocated():
    # 旧库写死的“异机”绝对路径（当前主机上不存在）应只取 mcp_servers/ 之后部分，重拼到当前 backend 根
    legacy = "/opt/old-deploy/backend/mcp_servers/refund_server.py"
    resolved = _resolve_stdio_arg(legacy)
    assert Path(resolved) == (_BACKEND_ROOT / "mcp_servers" / "refund_server.py").resolve()
    assert "opt" not in Path(resolved).parts and "old-deploy" not in Path(resolved).parts


def test_option_and_value_tokens_preserved():
    assert _resolve_stdio_arg("--port") == "--port"
    assert _resolve_stdio_arg("-v") == "-v"
    assert _resolve_stdio_arg("9000") == "9000"


# ---------------- server_source 组装 ----------------
def test_stdio_source_uses_runtime_interpreter_and_absolute_script():
    source = _stdio("", ["mcp_servers/logistics_server.py"])
    assert isinstance(source, list)
    assert source[0] == sys.executable
    assert Path(source[1]) == (_BACKEND_ROOT / "mcp_servers" / "logistics_server.py").resolve()


def test_stdio_source_keeps_extra_flags():
    source = _stdio("", ["mcp_servers/x.py", "--verbose"])
    assert source[0] == sys.executable
    assert Path(source[1]).name == "x.py"
    assert source[2] == "--verbose"


def test_sse_and_http_source_shape_unchanged():
    sse = _build_mcp_server_source(
        {"config": {"transport": "sse", "endpoint": "http://h:9000/sse", "headers": {"a": "b"}}}
    )
    assert sse == {"transport": "sse", "url": "http://h:9000/sse", "headers": {"a": "b"}}

    http = _build_mcp_server_source({"config": {"transport": "http", "endpoint": "http://h/mcp"}})
    assert http == {"transport": "http", "url": "http://h/mcp"}
