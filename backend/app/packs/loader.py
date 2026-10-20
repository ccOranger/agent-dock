"""场景包加载器：读取 `packs/<name>/manifest.yaml`，按声明装载包内 Agent 与工具。

设计约束（见 docs/domain-decoupling.md）：
- 内核不直接 `import app.packs.*`；包通过 manifest 反向注册，内核只消费这里的返回值。
- 单个包或单条资产装载失败只记日志并跳过（降级为「仅内核模式」），不阻塞服务启动。
- 结果按进程缓存：包属于代码资产，变更后需重启生效，与原有代码 Agent 的行为一致。
"""
from __future__ import annotations

import functools
import importlib
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PACKS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PackManifest:
    """场景包清单：只保留平台需要的通用字段，不含任何业务语义。"""

    name: str
    display_name: str
    version: str
    enabled: bool
    agents: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    knowledge_namespaces: tuple[str, ...] = ()
    # 运营台通用能力声明（如 tickets），由前端据此决定导航与页面是否可见
    features: tuple[str, ...] = ()
    root: Path = PACKS_DIR


def _parse_manifest(path: Path) -> PackManifest | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning(f"场景包清单解析失败，已跳过 {path}: {e}")
        return None
    name = str(raw.get("name") or path.parent.name).strip()
    if not name:
        logger.warning(f"场景包 name 为空，已跳过 {path}")
        return None
    knowledge = raw.get("knowledge") or {}
    return PackManifest(
        name=name,
        display_name=str(raw.get("display_name") or name),
        version=str(raw.get("version") or "0.0.0"),
        enabled=bool(raw.get("enabled", True)),
        agents=tuple(str(x) for x in raw.get("agents") or []),
        tools=tuple(str(x) for x in raw.get("tools") or []),
        knowledge_namespaces=tuple(str(x) for x in knowledge.get("namespaces") or []),
        features=tuple(str(x) for x in raw.get("features") or []),
        root=path.parent,
    )


def _scan_manifests() -> tuple[PackManifest, ...]:
    """扫描 `packs/*/manifest.yaml`，返回全部清单（含 manifest 停用的包，按名称排序）。"""
    manifests: list[PackManifest] = []
    for path in sorted(PACKS_DIR.glob("*/manifest.yaml")):
        manifest = _parse_manifest(path)
        if manifest is not None:
            manifests.append(manifest)
    return tuple(manifests)


@functools.lru_cache(maxsize=1)
def scan_manifests() -> tuple[PackManifest, ...]:
    """返回全部场景包清单（含停用包，供运营台展示；结果按进程缓存）。"""
    return _scan_manifests()


@functools.lru_cache(maxsize=1)
def load_manifests() -> tuple[PackManifest, ...]:
    """扫描 `packs/*/manifest.yaml`，返回已启用包的清单（按名称排序）。"""
    manifests: list[PackManifest] = []
    for manifest in _scan_manifests():
        if not manifest.enabled:
            logger.info(f"场景包已停用，跳过装载: {manifest.name}")
            continue
        manifests.append(manifest)
    return tuple(manifests)


def _import_symbol(ref: str) -> object:
    module_path, _, symbol = ref.partition(":")
    if not module_path or not symbol:
        raise ValueError(f"资产引用需为 module:Class 形式，实际为 {ref!r}")
    module = importlib.import_module(module_path)
    return getattr(module, symbol)


def _load_assets(refs: tuple[str, ...], base_type: type, kind: str) -> dict[str, type]:
    assets: dict[str, type] = {}
    for ref in refs:
        try:
            obj = _import_symbol(ref)
        except (ImportError, AttributeError, ValueError) as e:
            logger.warning(f"场景包{kind}装载失败，已跳过 {ref}: {e}")
            continue
        if not (isinstance(obj, type) and issubclass(obj, base_type)):
            logger.warning(f"场景包{kind}必须是 {base_type.__name__} 子类，已跳过 {ref}")
            continue
        name = getattr(obj, "name", "")
        if not name:
            logger.warning(f"场景包{kind}缺少 name 属性，已跳过 {ref}")
            continue
        if name in assets:
            logger.warning(f"场景包{kind}名称重复，忽略后者: {name} ({ref})")
            continue
        assets[name] = obj
    return assets


@functools.lru_cache(maxsize=1)
def get_code_agents() -> dict[str, type]:
    """返回全部场景包声明的代码 Agent：name -> Agent 类。"""
    from app.agents.base_business_agent import BaseBusinessAgent

    refs = tuple(ref for m in load_manifests() for ref in m.agents)
    return _load_assets(refs, BaseBusinessAgent, " Agent ")


@functools.lru_cache(maxsize=1)
def get_custom_tools() -> dict[str, type]:
    """返回全部场景包声明的自定义工具：name -> 工具类。"""
    from app.tools.custom.base import BaseTool

    refs = tuple(ref for m in load_manifests() for ref in m.tools)
    return _load_assets(refs, BaseTool, "工具")


def get_knowledge_namespaces() -> tuple[str, ...]:
    """返回全部启用包声明的知识库命名空间（去重保序，供后续检索范围收敛使用）。"""
    seen: list[str] = []
    for manifest in load_manifests():
        for ns in manifest.knowledge_namespaces:
            if ns not in seen:
                seen.append(ns)
    return tuple(seen)


def _asset_pack_map(attr: str) -> dict[str, str]:
    """按 manifest 顺序建立「资产 name -> 包 name」映射（重名以先登记者为准）。

    覆盖全部清单（含 manifest 停用的包），供运营台展示包内容；
    运行时装载仍以 load_manifests 为准。
    """
    mapping: dict[str, str] = {}
    for manifest in scan_manifests():
        for ref in getattr(manifest, attr):
            try:
                obj = _import_symbol(ref)
            except (ImportError, AttributeError, ValueError):
                continue
            name = getattr(obj, "name", "")
            if name and name not in mapping:
                mapping[name] = manifest.name
    return mapping


@functools.lru_cache(maxsize=1)
def get_code_agent_packs() -> dict[str, str]:
    """返回清单声明的代码 Agent 归属：agent name -> pack name（含停用包）。"""
    return _asset_pack_map("agents")


@functools.lru_cache(maxsize=1)
def get_custom_tool_packs() -> dict[str, str]:
    """返回清单声明的自定义工具归属：tool name -> pack name（含停用包）。"""
    return _asset_pack_map("tools")


@functools.lru_cache(maxsize=1)
def get_enabled_features() -> tuple[str, ...]:
    """返回全部启用包声明的运营台功能（去重保序，如 tickets）。"""
    seen: list[str] = []
    for manifest in load_manifests():
        for feature in manifest.features:
            if feature not in seen:
                seen.append(feature)
    return tuple(seen)
