"""场景运行参数：白名单校验、继承语义与来源标注。

守两件事：
- 写错键必须 400（"配了但没生效"是后台最难查的一类问题）；
- 解析必须是纯函数：缺失项继承全局，场景覆盖项标注来源，额度缺失按"不限"。
"""
import pytest

from app.core.config import settings
from app.core.exceptions import ParamError
from app.schemas.scene import DEFAULT_HISTORY_LIMIT, SceneRuntime
from app.services.runtime_config import (
    effective_runtime,
    normalize_runtime_config,
    resolve_runtime,
    runtime_effective_dict,
)


# ===== 写入侧：规范化与校验 =====
def test_normalize_empty_means_inherit_all():
    assert normalize_runtime_config(None) == {}
    assert normalize_runtime_config({}) == {}
    # 子对象全为 null 时不留空壳，避免后台显示"已覆盖"却没有任何值
    assert normalize_runtime_config({"rag": {"top_k": None}}) == {}


def test_normalize_rejects_unknown_keys():
    """未知键静默丢弃会让运营以为已生效，必须直接拒绝"""
    with pytest.raises(ParamError, match="未知键"):
        normalize_runtime_config({"unknown": 1})
    with pytest.raises(ParamError, match="未知键"):
        normalize_runtime_config({"rag": {"model": "gpt"}})
    with pytest.raises(ParamError, match="未知键"):
        normalize_runtime_config({"quota": {"per_hour": 10}})


def test_normalize_rejects_out_of_range_and_wrong_types():
    with pytest.raises(ParamError, match="rag.top_k"):
        normalize_runtime_config({"rag": {"top_k": 0}})
    with pytest.raises(ParamError, match="rag.top_k"):
        normalize_runtime_config({"rag": {"top_k": "5"}})
    # bool 是 int 的子类：不排除会变成 top_k=True
    with pytest.raises(ParamError, match="rag.top_k"):
        normalize_runtime_config({"rag": {"top_k": True}})
    with pytest.raises(ParamError, match="rag.min_score"):
        normalize_runtime_config({"rag": {"min_score": 1.5}})
    with pytest.raises(ParamError, match="history_limit"):
        normalize_runtime_config({"history_limit": DEFAULT_HISTORY_LIMIT + 1})
    with pytest.raises(ParamError, match="quota.per_minute"):
        normalize_runtime_config({"quota": {"per_minute": 601}})


def test_normalize_namespaces_strips_and_dedupes():
    config = normalize_runtime_config({"rag": {"namespaces": [" cs_order ", "cs_order", "cs_it"]}})
    assert config["rag"]["namespaces"] == ["cs_order", "cs_it"]
    # 允许显式清空：空数组=不兜底检索
    assert normalize_runtime_config({"rag": {"namespaces": []}}) == {"rag": {"namespaces": []}}
    with pytest.raises(ParamError, match="最多"):
        normalize_runtime_config({"rag": {"namespaces": [f"ns{i}" for i in range(11)]}})
    with pytest.raises(ParamError, match="非空字符串"):
        normalize_runtime_config({"rag": {"namespaces": [""]}})


def test_normalize_keeps_explicit_zero_quota():
    """0 是"不限"，不能被当成假值丢掉——否则后台关掉额度会被判成非法输入"""
    assert normalize_runtime_config({"quota": {"per_minute": 0, "daily": 0}}) == {
        "quota": {"per_minute": 0, "daily": 0}
    }


def test_normalize_history_limit_accepts_boundaries():
    assert normalize_runtime_config({"history_limit": 1}) == {"history_limit": 1}
    assert normalize_runtime_config({"history_limit": DEFAULT_HISTORY_LIMIT}) == {
        "history_limit": DEFAULT_HISTORY_LIMIT
    }


# ===== 读取侧：继承与来源 =====
def test_resolve_inherits_global_defaults(monkeypatch):
    monkeypatch.setattr(settings, "rag_top_k", 7)
    monkeypatch.setattr(settings, "rag_min_score", 0.42)
    monkeypatch.setattr(settings, "rag_timeout", 9)

    runtime = resolve_runtime({})

    assert runtime.rag_inject is True
    assert runtime.rag_top_k == 7
    assert runtime.rag_min_score == 0.42
    assert runtime.rag_timeout == 9
    assert runtime.history_limit == DEFAULT_HISTORY_LIMIT
    assert runtime.rag_namespaces == ()
    assert runtime.sources["rag.top_k"] == "global"
    assert runtime.sources["history_limit"] == "global"


def test_resolve_marks_scene_overrides(monkeypatch):
    monkeypatch.setattr(settings, "rag_top_k", 7)
    runtime = resolve_runtime({"rag": {"top_k": 3, "namespaces": ["cs_order"]}})

    assert runtime.rag_top_k == 3
    assert runtime.sources["rag.top_k"] == "scene"
    assert runtime.sources["rag.min_score"] == "global"
    assert runtime.rag_namespaces == ("cs_order",)


def test_resolve_quota_defaults_to_unlimited(monkeypatch):
    """场景总额度不能继承"单主体"限流：否则 30 次/分 会变成整个场景的总额"""
    monkeypatch.setattr(settings, "public_chat_rate_limit_per_minute", 30)
    monkeypatch.setattr(settings, "public_chat_daily_quota", 2000)

    runtime = resolve_runtime({})

    assert runtime.quota.per_minute == 0
    assert runtime.quota.daily == 0
    assert runtime.sources["quota.daily"] == "default"


def test_effective_runtime_accepts_prebuilt_snapshot():
    """热路径只解析一次：已算好的快照不再重复合并全局默认"""
    snapshot = SceneRuntime(rag_top_k=2, history_limit=10)
    assert effective_runtime(SimpleScene(runtime=snapshot)) is snapshot


def test_effective_runtime_falls_back_to_config():
    scene = SimpleScene(runtime_config={"rag": {"top_k": 4}})
    runtime = effective_runtime(scene)
    assert runtime.rag_top_k == 4
    # 手工构造的历史场景对象没有这两个字段也不能炸
    assert effective_runtime(None).rag_top_k == settings.rag_top_k


def test_runtime_effective_dict_shape():
    data = runtime_effective_dict(resolve_runtime({"history_limit": 12}))
    assert data["history_limit"] == 12
    assert set(data["rag"]) == {"inject", "top_k", "min_score", "timeout", "namespaces"}
    assert data["rag"]["namespaces"] == []
    assert data["quota"] == {"per_minute": 0, "daily": 0}


class SimpleScene:
    """最小场景替身：只带解析运行参数用到的字段。"""

    def __init__(self, runtime=None, runtime_config=None):
        if runtime is not None:
            self.runtime = runtime
        if runtime_config is not None:
            self.runtime_config = runtime_config
