"""场景包装载器（packs/loader）的行为契约测试。

覆盖：manifest 扫描与字段校验、包内 Agent/工具装载、非法引用降级跳过、
知识库命名空间去重，以及内核注册表确实以 manifest 为唯一来源。
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from app.agents import registry
from app.agents.base_business_agent import BaseBusinessAgent
from app.packs import loader
from app.tools import custom_catalog
from app.tools.custom.base import BaseTool


@pytest.fixture
def pack_root():
    """独立临时目录：不依赖 tmp_path（系统 TEMP 下的 pytest 根目录可能权限异常）。"""
    root = Path(tempfile.mkdtemp(prefix="packs-loader-"))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _write_manifest(root: Path, dir_name: str, body: str) -> Path:
    pack_dir = root / dir_name
    pack_dir.mkdir()
    path = pack_dir / "manifest.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ===== 现有客服包 =====
def test_load_manifests_discovers_customer_service():
    manifest = next((m for m in loader.load_manifests() if m.name == "customer_service"), None)
    assert manifest is not None
    assert manifest.enabled is True
    assert manifest.display_name
    assert len(manifest.agents) == 3
    assert len(manifest.tools) == 9


def test_code_agents_loaded_from_manifest():
    agents = loader.get_code_agents()
    assert set(agents) == {"knowledge_qa", "order_service", "complaint_handler"}
    assert all(isinstance(cls, type) and issubclass(cls, BaseBusinessAgent) for cls in agents.values())


def test_custom_tools_loaded_from_manifest():
    tools = loader.get_custom_tools()
    assert set(tools) == {
        "query_order",
        "list_orders",
        "modify_order",
        "query_logistics",
        "submit_refund",
        "query_refund",
        "create_ticket",
        "update_ticket",
        "escalate",
    }
    assert all(isinstance(cls, type) and issubclass(cls, BaseTool) for cls in tools.values())


def test_kernel_registries_follow_manifest():
    """内核注册表只消费 manifest 装载结果，不再硬编码包内类。"""
    assert set(registry.AGENT_REGISTRY) == set(loader.get_code_agents())
    assert set(custom_catalog.CATALOG) == set(loader.get_custom_tools())


def test_knowledge_namespaces_deduplicated_in_order():
    namespaces = loader.get_knowledge_namespaces()
    assert namespaces == ("cs_default", "cs_order", "cs_after_sales")
    assert len(namespaces) == len(set(namespaces))


# ===== manifest 解析降级 =====
def test_parse_manifest_skips_broken_yaml(pack_root):
    path = _write_manifest(pack_root, "broken", "name: [unclosed\n")
    assert loader._parse_manifest(path) is None


def test_parse_manifest_defaults_missing_fields(pack_root):
    path = _write_manifest(pack_root, "minimal", "enabled: true\n")
    manifest = loader._parse_manifest(path)
    assert manifest is not None
    assert manifest.name == "minimal"  # 缺 name 时回退目录名
    assert manifest.display_name == "minimal"
    assert manifest.version == "0.0.0"
    assert manifest.agents == ()
    assert manifest.knowledge_namespaces == ()


def test_load_manifests_skips_disabled_pack(pack_root, monkeypatch):
    _write_manifest(pack_root, "on_pack", "name: on_pack\nenabled: true\n")
    _write_manifest(pack_root, "off_pack", "name: off_pack\nenabled: false\n")
    monkeypatch.setattr(loader, "PACKS_DIR", pack_root)
    loader.load_manifests.cache_clear()
    try:
        assert [m.name for m in loader.load_manifests()] == ["on_pack"]
    finally:
        loader.load_manifests.cache_clear()


# ===== 资产装载降级 =====
def test_load_assets_skips_invalid_and_duplicate_refs(monkeypatch):
    class _NoName(BaseTool):
        name = ""

    class _Dup(BaseTool):
        name = "dup"

    class _Dup2(BaseTool):
        name = "dup"

    mapping = {
        "fake:Good": type("Good", (BaseTool,), {"name": "good"}),
        "fake:NoName": _NoName,
        "fake:Dup": _Dup,
        "fake:Dup2": _Dup2,
        "fake:NotSubclass": object,
    }

    def fake_import(ref: str):
        if ref not in mapping:
            raise ImportError(ref)
        return mapping[ref]

    monkeypatch.setattr(loader, "_import_symbol", fake_import)
    assets = loader._load_assets(
        (
            "fake:Good",
            "fake:NoName",
            "fake:Dup",
            "fake:Dup2",
            "fake:NotSubclass",
            "missing:Thing",
        ),
        BaseTool,
        "工具",
    )
    # 命中、空名、重名后者、非子类、导入失败：只保留合法且首个注册者
    assert set(assets) == {"good", "dup"}
    assert assets["dup"] is _Dup


def test_import_symbol_rejects_bad_ref():
    with pytest.raises(ValueError):
        loader._import_symbol("no_colon_here")
