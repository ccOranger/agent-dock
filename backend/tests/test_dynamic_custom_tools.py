"""动态自定义工具（方案 A：后台在线写源码 + 安全校验 + 指纹缓存编译）。

覆盖三层：
- tool_security：AST 白名单/黑名单（不执行任何用户代码）
- tool_code_loader：编译、类校验、按指纹缓存复用、失败不污染旧缓存
- custom_loader / admin API：两来源统一装配与后台接口的校验/试运行/落库
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from hello_agents.tools import ToolRegistry

from app.api.v1 import admin as adm
from app.core.exceptions import ParamError
from app.models.repositories import ToolRepo
from app.tools import custom_catalog, tool_code_loader, tool_security
from app.tools.custom_loader import fetch_dynamic_configs, register_custom_tools

VALID_CODE = """
class WeatherTool(BaseTool):
    name = "demo_weather"
    description = "查询演示城市天气。仅在客户询问天气时调用。"
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名"},
        },
        "required": ["city"],
        "additionalProperties": False,
    }

    async def run(self, city: str):
        return {"ok": True, "data": {"city": city, "weather": "晴"}}
"""

# 把 VALID_CODE 的工具名换掉（用于「类内 name 与登记名一致性」用例）
def _renamed(new_name: str) -> str:
    return VALID_CODE.replace('name = "demo_weather"', f'name = "{new_name}"')


# ===== AST 安全校验 =====
def test_security_allows_whitelisted_imports():
    code = (
        "import json\nfrom datetime import datetime\nimport httpx\nimport asyncio\n"
        "from collections import defaultdict\nfrom urllib.parse import urlencode\n"
        + 'class T(BaseTool):\n    name = "t"\n    description = "d"\n'
        "    parameters = {'type': 'object', 'properties': {}}\n"
        "    async def run(self):\n        "
        "return json.dumps({'ok': True, 'q': urlencode({'a': 1}), 'h': defaultdict(list)})\n"
    )
    ok, errors = tool_security.check_source(code)
    assert ok, errors


@pytest.mark.parametrize(
    "snippet,keyword",
    [
        ("import os", "禁止导入模块: os"),
        ("from asyncio.subprocess import shell", "禁止导入模块: asyncio.subprocess"),
        ("asyncio.create_subprocess_shell('calc')", "禁止使用 create_subprocess_shell"),
        ("from pathlib import Path", "禁止导入模块: pathlib"),
        ("import subprocess as sp", "禁止导入模块: subprocess"),
        ("from app.tools.custom import mock_data", "禁止导入模块: app"),
        ("from . import helpers", "禁止相对导入"),
        ("from json import *", "禁止 from json import *"),
        ("open('/etc/passwd')", "禁止调用 open()"),
        ("eval('1+1')", "禁止调用 eval()"),
        ("exec('x=1')", "禁止调用 exec()"),
        ("__import__('os')", "禁止调用 __import__()"),
        ("getattr(obj, 'x')", "禁止调用 getattr()"),
        ("f.__globals__", "禁止访问属性 __globals__"),
        ("obj.__class__.__subclasses__()", "禁止访问属性 __class__"),
    ],
)
def test_security_blocks_dangerous_patterns(snippet, keyword):
    ok, errors = tool_security.check_source(f"def _f():\n    {snippet}\n    return 1\n")
    assert ok is False
    assert any(keyword in e for e in errors), errors


def test_security_rejects_empty_oversize_and_syntax_error():
    assert tool_security.check_source("")[1]
    assert tool_security.check_source("   \n")[1]
    ok, errors = tool_security.check_source("def f(:\n")
    assert ok is False and any("语法错误" in e for e in errors)
    ok, errors = tool_security.check_source("x = 1\n" * 500)
    assert ok is False and any("超过上限" in e for e in errors)


# ===== 元信息静态提取（不执行代码）=====
def test_extract_tool_meta_reads_class_attributes():
    ok, meta, errors = tool_security.extract_tool_meta(VALID_CODE)
    assert ok, errors
    assert meta["name"] == "demo_weather"
    assert meta["description"].startswith("查询演示城市天气")
    assert meta["parameters"]["properties"]["city"]["type"] == "string"


def test_extract_tool_meta_requires_base_tool_subclass():
    ok, _, errors = tool_security.extract_tool_meta("def run():\n    return 1\n")
    assert ok is False
    assert any("BaseTool" in e for e in errors)


def test_extract_tool_meta_rejects_missing_description():
    code = VALID_CODE.replace('    description = "查询演示城市天气。仅在客户询问天气时调用。"\n', "")
    ok, _, errors = tool_security.extract_tool_meta(code)
    assert ok is False and any("description" in e for e in errors)


# ===== 编译器 =====
def test_compile_tool_success_and_injected_basetool():
    cls, errors = tool_code_loader.compile_tool(VALID_CODE, expected_name="demo_weather")
    assert cls is not None, errors
    assert cls.name == "demo_weather"
    # 项目基类：async run + get_parameters 把 JSON Schema 转成 ToolParameter 列表
    tool = cls()
    assert [p.name for p in tool.get_parameters()] == ["city"]


def test_compile_tool_rejects_name_mismatch():
    cls, errors = tool_code_loader.compile_tool(VALID_CODE, expected_name="other_name")
    assert cls is None
    assert any("不一致" in e for e in errors)


def test_compile_tool_rejects_unsafe_code_before_exec():
    cls, errors = tool_code_loader.compile_tool(
        "import os\nclass T(BaseTool):\n    name = \"t\"\n    description = \"d\"\n"
        "    parameters = {'type': 'object', 'properties': {}}\n"
        "    async def run(self):\n        return os.getcwd()\n"
    )
    assert cls is None and any("禁止导入模块: os" in e for e in errors)


def test_compile_tool_rejects_multiple_classes():
    code = VALID_CODE + "\n\nclass SecondTool(BaseTool):\n    name = \"second\"\n    description = \"d\"\n"
    cls, errors = tool_code_loader.compile_tool(code)
    assert cls is None and any("一个工具类" in e for e in errors)


def test_compile_tool_blocks_dangerous_builtins_at_runtime():
    """AST 之外的第二层：受限 builtins 里根本没有 open，运行时也调不到。"""
    code = (
        'class T(BaseTool):\n    name = "t"\n    description = "d"\n'
        "    parameters = {'type': 'object', 'properties': {}}\n"
        "    async def run(self):\n        return f.read() if (f := None) else 1\n"
        "    def peek(self):\n        return open('x')\n"
    )
    cls, errors = tool_code_loader.compile_tool(code)
    assert cls is None and any("禁止调用 open()" in e for e in errors)


# ===== 运行期导入守卫（受限 builtins 不能把合法 import 也封死）=====
def test_runtime_import_guard_allows_whitelist_and_blocks_denied():
    import json as _json

    assert tool_code_loader._guarded_import("json") is not None
    assert tool_code_loader._guarded_import("json", fromlist=("dumps",)).dumps is _json.dumps
    with pytest.raises(ImportError):
        tool_code_loader._guarded_import("os")
    with pytest.raises(ImportError):
        tool_code_loader._guarded_import("asyncio.subprocess")
    with pytest.raises(ImportError):
        tool_code_loader._guarded_import("helpers", level=1)


def test_dynamic_tool_can_use_whitelisted_module_at_runtime():
    """带白名单 import 的源码能真编译并执行（剔了 __import__ 会整条链路挂掉）。"""
    code = (
        "import json\nfrom datetime import date\n"
        'class T(BaseTool):\n    name = "t"\n    description = "d"\n'
        "    parameters = {'type': 'object', 'properties': {}}\n\n"
        "    async def run(self):\n"
        '        return {"ok": True, "data": json.loads("[1, 2]"), "day": date(2026, 11, 2).isoformat()}\n'
    )
    cls, errors = tool_code_loader.compile_tool(code, expected_name="t")
    assert cls is not None, errors
    import asyncio

    assert asyncio.run(cls().run()) == {"ok": True, "data": [1, 2], "day": "2026-11-02"}


def test_compile_tool_clamps_oversize_timeout():
    code = VALID_CODE.replace(
        "    async def run(self, city: str):", "    timeout = 9999\n\n    async def run(self, city: str):"
    )
    cls, errors = tool_code_loader.compile_tool(code, expected_name="demo_weather")
    assert cls is not None, errors
    assert cls.timeout == tool_code_loader._MAX_TOOL_TIMEOUT


# ===== 指纹缓存 =====
def test_cache_reuses_class_until_code_changes():
    tool_code_loader.invalidate("demo_weather")
    cls1, _ = tool_code_loader.get_compiled_class("demo_weather", VALID_CODE)
    cls2, _ = tool_code_loader.get_compiled_class("demo_weather", VALID_CODE)
    assert cls1 is cls2  # 同一指纹：零编译复用
    changed = VALID_CODE.replace("查询演示城市天气", "查询演示城市天气（v2）")
    cls3, errors = tool_code_loader.get_compiled_class("demo_weather", changed)
    assert cls3 is not None and cls3 is not cls1
    assert "（v2）" in cls3.description
    # 坏代码不覆盖已缓存的可用版本
    bad_cls, bad_errors = tool_code_loader.get_compiled_class("demo_weather", "import os\n" + changed)
    assert bad_cls is None and bad_errors
    cached, _ = tool_code_loader.get_compiled_class("demo_weather", changed)
    assert cached is cls3
    tool_code_loader.invalidate("demo_weather")


def test_build_returns_instance_or_none():
    assert tool_code_loader.build("demo_weather", "import os\n" + VALID_CODE) is None
    tool = tool_code_loader.build("demo_weather", VALID_CODE)
    assert tool is not None and tool.name == "demo_weather"
    tool_code_loader.invalidate("demo_weather")


# ===== 注册器：两来源统一装配 =====
def test_register_custom_tools_mixes_code_and_dynamic():
    reg = ToolRegistry()
    registered = register_custom_tools(
        reg,
        ["query_order", "demo_weather", "ghost_tool"],
        {"demo_weather": {"code": VALID_CODE}},
    )
    # 代码目录工具照常、动态工具按源码装配、两者都不是的跳过
    assert registered == ["query_order", "demo_weather"]
    assert "demo_weather" in reg.list_tools()


def test_register_custom_tools_prefers_code_catalog_on_conflict():
    """同名时代码目录优先：即便 DB 里塞了同名的动态源码也不覆盖线上实现。"""
    reg = ToolRegistry()
    hijack = _renamed("query_order")
    register_custom_tools(reg, ["query_order"], {"query_order": {"code": hijack}})
    tool = reg.get_tool("query_order")
    assert "订单号" in (tool.description or "")  # 仍是代码目录那份描述


def test_register_custom_tools_skips_dynamic_without_prefetched_config():
    reg = ToolRegistry()
    assert register_custom_tools(reg, ["demo_weather"], None) == []
    assert register_custom_tools(reg, ["demo_weather"], {"demo_weather": {"code": ""}}) == []


@pytest.mark.asyncio
async def test_fetch_dynamic_configs_filters_and_respects_enabled(monkeypatch):
    rows = [
        SimpleNamespace(name="demo_weather", enabled=True, config={"code": VALID_CODE, "description": "覆盖"}),
        SimpleNamespace(name="disabled_tool", enabled=False, config={"code": VALID_CODE}),
        SimpleNamespace(name="legacy_row", enabled=True, config={"description": "仅登记未填源码"}),
        SimpleNamespace(name="query_order", enabled=True, config={"code": VALID_CODE}),  # 代码目录已有→不预取
    ]

    async def fake_list_by_type(type_):
        assert type_ == "custom"
        return rows

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    configs = await fetch_dynamic_configs(["demo_weather", "disabled_tool", "legacy_row", "query_order", "ghost"])
    assert set(configs) == {"demo_weather"}
    assert configs["demo_weather"]["code"] == VALID_CODE
    assert configs["demo_weather"]["description"] == "覆盖"


# ===== 后台接口 =====
@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([adm.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def _no_existing_rows(monkeypatch):
    async def fake_list_by_type(type_):
        return []

    async def fake_get_by_name(name):
        return None

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    monkeypatch.setattr(ToolRepo, "get_by_name", fake_get_by_name)


def test_create_dynamic_tool_persists_code_and_schema(admin_client, monkeypatch):
    _no_existing_rows(monkeypatch)
    captured: dict = {}

    async def fake_create_custom(name, schema_=None, config=None, enabled=True):
        captured.update({"name": name, "schema": schema_, "config": config, "enabled": enabled})
        return SimpleNamespace(name=name, id="x")

    async def fake_audit(**kw):
        return None

    monkeypatch.setattr(ToolRepo, "create_custom", fake_create_custom)
    monkeypatch.setattr(adm.AuditRepo, "write", fake_audit)

    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/dynamic",
        json={"name": "demo_weather", "code": VALID_CODE, "description": "后台显示名"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["name"] == "demo_weather"
    assert captured["config"]["source"] == "dynamic"
    assert captured["config"]["code"] == VALID_CODE
    assert captured["config"]["description"] == "后台显示名"
    assert captured["config"]["code_sha256"]
    assert captured["schema"]["properties"]["city"]["type"] == "string"
    assert captured["schema"]["description"].startswith("查询演示城市天气")


def test_create_dynamic_tool_rejects_unsafe_code(admin_client, monkeypatch):
    _no_existing_rows(monkeypatch)
    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/dynamic",
        json={"name": "demo_weather", "code": "import os\n" + VALID_CODE},
    )
    assert resp.status_code == 400
    assert "禁止导入模块: os" in resp.json()["detail"]["message"]


def test_create_dynamic_tool_rejects_name_taken_by_code_catalog(admin_client, monkeypatch):
    _no_existing_rows(monkeypatch)
    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/dynamic",
        json={"name": "query_order", "code": _renamed("query_order")},
    )
    assert resp.status_code == 409


def test_list_custom_tools_includes_dynamic_rows(admin_client, monkeypatch):
    dyn = SimpleNamespace(
        name="demo_weather",
        enabled=True,
        schema_={"type": "object", "properties": {"city": {}}, "description": "代码描述"},
        config={"source": "dynamic", "code": VALID_CODE, "code_updated_at": "2026-11-02T01:00:00+00:00"},
    )
    legacy = SimpleNamespace(name="orphan_row", enabled=True, schema_={}, config={"description": "无源码旧行"})

    async def fake_list_by_type(type_):
        return [dyn, legacy]

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    items = {d["name"]: d for d in admin_client.get(f"{adm.router.prefix}/custom-tools").json()["data"]}
    assert items["demo_weather"]["source"] == "dynamic"
    assert items["demo_weather"]["has_code"] is True
    assert items["demo_weather"]["description"] == "代码描述"
    assert items["query_order"]["source"] == "code"
    assert "orphan_row" not in items  # 无源码的历史行不混进列表


def test_get_and_update_dynamic_code(admin_client, monkeypatch):
    row = SimpleNamespace(
        id="x", name="demo_weather", enabled=True, schema_={}, config={"source": "dynamic", "code": VALID_CODE}
    )
    updated: dict = {}

    async def fake_list_by_type(type_):
        return [row]

    async def fake_update(tool_id, **fields):
        updated.update(fields)
        return row

    async def fake_audit(**kw):
        return None

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    monkeypatch.setattr(ToolRepo, "update", fake_update)
    monkeypatch.setattr(adm.AuditRepo, "write", fake_audit)

    got = admin_client.get(f"{adm.router.prefix}/custom-tools/demo_weather/code")
    assert got.status_code == 200
    assert got.json()["data"]["code"] == VALID_CODE

    new_code = VALID_CODE.replace("晴", "多云")
    resp = admin_client.put(
        f"{adm.router.prefix}/custom-tools/demo_weather/code",
        json={"code": new_code, "description": ""},
    )
    assert resp.status_code == 200
    assert updated["config"]["code"] == new_code
    assert "description" not in updated["config"]  # 空串=清除覆盖
    assert updated["schema_"]["description"].startswith("查询演示城市天气")


def test_update_dynamic_code_rejects_code_catalog_tool(admin_client, monkeypatch):
    row = SimpleNamespace(id="x", name="query_order", enabled=True, schema_={}, config={})

    async def fake_list_by_type(type_):
        return [row]

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    resp = admin_client.put(
        f"{adm.router.prefix}/custom-tools/query_order/code",
        json={"code": VALID_CODE},
    )
    assert resp.status_code == 404


def test_validate_endpoint_reports_errors_without_persisting(admin_client):
    ok = admin_client.post(f"{adm.router.prefix}/custom-tools/validate", json={"code": VALID_CODE}).json()["data"]
    assert ok["ok"] is True and ok["name"] == "demo_weather"
    bad = admin_client.post(
        f"{adm.router.prefix}/custom-tools/validate",
        json={"code": VALID_CODE, "name": "other_name"},
    ).json()["data"]
    assert bad["ok"] is False and any("不一致" in e for e in bad["errors"])


def test_test_run_executes_tool(admin_client, monkeypatch):
    async def fake_audit(**kw):
        return None

    monkeypatch.setattr(adm.AuditRepo, "write", fake_audit)
    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/test-run",
        json={"name": "demo_weather", "code": VALID_CODE, "args": {"city": "北京"}},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["result"] == {"ok": True, "data": {"city": "北京", "weather": "晴"}}


def test_test_run_uses_stored_code_when_omitted(admin_client, monkeypatch):
    row = SimpleNamespace(id="x", name="demo_weather", enabled=True, schema_={}, config={"code": VALID_CODE})

    async def fake_list_by_type(type_):
        return [row]

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/test-run",
        json={"name": "demo_weather", "args": {"city": "上海"}},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["result"]["data"]["city"] == "上海"


def test_test_run_reports_runtime_exception(admin_client):
    code = VALID_CODE.replace("return {\"ok\": True", 'raise RuntimeError("下游接口不可用") or {"ok": True')
    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/test-run",
        json={"name": "demo_weather", "code": code, "args": {"city": "北京"}},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False and "下游接口不可用" in data["error"]


def test_test_run_rejects_unsafe_code(admin_client):
    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/test-run",
        json={"name": "demo_weather", "code": "open('/etc/passwd')\n" + VALID_CODE},
    )
    assert resp.status_code == 400
    assert "代码校验未通过" in resp.json()["detail"]["message"]


# ===== 动态 Agent 绑定校验：允许动态工具 =====
@pytest.mark.asyncio
async def test_validate_bindings_accepts_dynamic_tool(monkeypatch):
    """不在代码目录但已登记且源码可编译的动态工具，可以被 Agent 绑定。"""
    dyn = SimpleNamespace(name="demo_weather", enabled=True, schema_={}, config={"code": VALID_CODE})

    async def fake_list_by_type(type_):
        return [dyn]

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    await adm._validate_custom_tool_bindings(["demo_weather"])  # 不抛异常即通过


@pytest.mark.asyncio
async def test_validate_bindings_rejects_broken_dynamic_code(monkeypatch):
    dyn = SimpleNamespace(name="demo_weather", enabled=True, schema_={}, config={"code": "import os\n" + VALID_CODE})

    async def fake_list_by_type(type_):
        return [dyn]

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    with pytest.raises(ParamError) as exc:
        await adm._validate_custom_tool_bindings(["demo_weather"])
    assert "源码不可用" in str(exc.value)


@pytest.mark.asyncio
async def test_validate_bindings_rejects_registered_without_code(monkeypatch):
    """已登记但无源码、也不在代码目录 → 无可调用实现，绑定时拦住。"""
    dyn = SimpleNamespace(name="demo_weather", enabled=True, schema_={}, config={"description": "仅有元信息"})

    async def fake_list_by_type(type_):
        return [dyn]

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    with pytest.raises(ParamError):
        await adm._validate_custom_tool_bindings(["demo_weather"])


def test_create_agent_rejects_dynamic_tool_with_broken_code(admin_client, monkeypatch):
    dyn = SimpleNamespace(name="demo_weather", enabled=True, schema_={}, config={"code": "import os\n" + VALID_CODE})

    async def fake_agent_get(name):
        return None

    async def fake_list_by_type(type_):
        return [dyn]

    monkeypatch.setattr(adm.AgentRepo, "get_by_name", fake_agent_get)
    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    resp = admin_client.post(
        f"{adm.router.prefix}/agents",
        json={
            "name": "d_bad",
            "description": "演示",
            "system_prompt": "你是助手",
            "mcp_tool_names": [],
            "custom_tool_names": ["demo_weather"],
        },
    )
    assert resp.status_code == 400
    assert "源码不可用" in resp.json()["detail"]["message"]


def test_create_agent_still_rejects_unknown_tool(admin_client, monkeypatch):
    async def fake_agent_get(name):
        return None

    async def fake_list_by_type(type_):
        return []

    monkeypatch.setattr(adm.AgentRepo, "get_by_name", fake_agent_get)
    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    resp = admin_client.post(
        f"{adm.router.prefix}/agents",
        json={
            "name": "d_ghost",
            "description": "演示",
            "system_prompt": "你是助手",
            "mcp_tool_names": [],
            "custom_tool_names": ["ghost_tool"],
        },
    )
    assert resp.status_code == 400


def test_delete_dynamic_tool_invalidates_cache(admin_client, monkeypatch):
    tool_code_loader.get_compiled_class("demo_weather", VALID_CODE)  # 先写入缓存
    row = SimpleNamespace(id="x", name="demo_weather", enabled=True, schema_={}, config={"code": VALID_CODE})

    async def fake_list_by_type(type_):
        return [row]

    async def fake_list_dynamic(enabled_only=True):
        return []

    async def fake_delete(tool_id):
        return True

    async def fake_audit(**kw):
        return None

    monkeypatch.setattr(ToolRepo, "list_by_type", fake_list_by_type)
    monkeypatch.setattr(adm.AgentRepo, "list_dynamic", fake_list_dynamic)
    monkeypatch.setattr(ToolRepo, "delete", fake_delete)
    monkeypatch.setattr(adm.AuditRepo, "write", fake_audit)

    resp = admin_client.delete(f"{adm.router.prefix}/custom-tools/demo_weather")
    assert resp.status_code == 200
    assert "demo_weather" not in tool_code_loader._cache


def test_code_catalog_tools_are_not_editable_as_dynamic(admin_client, monkeypatch):
    """代码目录工具在列表里标 source=code，后台不能给它写源码（避免同名双实现）。"""
    assert custom_catalog.exists("query_order")
    resp = admin_client.post(
        f"{adm.router.prefix}/custom-tools/dynamic",
        json={"name": "query_order", "code": _renamed("query_order")},
    )
    assert resp.status_code in (400, 409)


# ===== 真实执行链路：动态工具走 orchestrator._execute_tool =====
_CONTEXT_CODE = '''
class WhoTool(BaseTool):
    name = "demo_who"
    description = "回显会话身份。"
    parameters = {
        "type": "object",
        "properties": {
            "word": {"type": "string", "description": "重复一次的内容"},
            "user_id": {"type": "string", "description": "内部参数，模型无需填写"},
        },
        "required": ["word"],
        "additionalProperties": False,
    }

    async def run(self, word: str, user_id: str = ""):
        return {"ok": True, "data": {"word": word, "user_id": user_id}}
'''


def _fake_simple_agent(registry):
    return type("S", (), {"tool_registry": registry})()


def test_execute_tool_runs_dynamic_tool_and_injects_session_context():
    """动态工具与代码目录工具走同一个执行器：会话上下文注入 + 结果文本包装。"""
    from app.agents.orchestrator import _execute_tool

    registry = ToolRegistry()
    register_custom_tools(registry, ["demo_who"], {"demo_who": {"code": _CONTEXT_CODE}})
    result = _execute_tool(
        _fake_simple_agent(registry),
        "demo_who",
        {"word": "hi"},
        {"user_id": "u-9", "session_id": "s-9"},
        None,
    )
    assert "demo_who" in result
    assert "u-9" in result and "hi" in result
    tool_code_loader.invalidate("demo_who")


def test_execute_tool_timeout_on_hanging_dynamic_tool():
    """永久等待的动态工具不会挂住整轮对话：类属性 timeout 到点即返回错误文本。

    两条路径都要上硬超时：生产路径是回投主循环（loop 非空），单测/脚本直调是
    loop=None 分支——后者曾会等工具自己跑完，所以一并验。
    """
    import asyncio
    import threading
    import time

    from app.agents.orchestrator import _execute_tool

    slow_code = _CONTEXT_CODE.replace(
        '    async def run(self, word: str, user_id: str = ""):\n'
        '        return {"ok": True, "data": {"word": word, "user_id": user_id}}',
        '    timeout = 0.2\n\n'
        '    async def run(self, word: str, user_id: str = ""):\n'
        "        await asyncio.sleep(30)\n"
        '        return {"ok": True}',
    )
    # 需要显式用 asyncio（多点并发拉取）：已列入白名单
    slow_code = "import asyncio\n" + slow_code

    registry = ToolRegistry()
    register_custom_tools(registry, ["demo_who"], {"demo_who": {"code": slow_code}})
    assert "demo_who" in registry.list_tools()

    def _assert_timed_out(result: str, elapsed: float) -> None:
        assert result.startswith("❌") and "超时" in result
        assert elapsed < 5  # 真被 timeout 掉断，而不是等完 30s

    # 1) loop=None
    started = time.perf_counter()
    _assert_timed_out(
        _execute_tool(_fake_simple_agent(registry), "demo_who", {"word": "hi"}, {"user_id": "u-1"}, None),
        time.perf_counter() - started,
    )

    # 2) 生产形状：工具回投到外部主循环，由 run_coroutine_threadsafe + future.result(timeout) 掉断
    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    try:
        started = time.perf_counter()
        _assert_timed_out(
            _execute_tool(
                _fake_simple_agent(registry), "demo_who", {"word": "hi"}, {"user_id": "u-1"}, loop
            ),
            time.perf_counter() - started,
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=2)
    tool_code_loader.invalidate("demo_who")
