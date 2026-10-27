"""登录人资源配额：语义口径、超限拒绝与"校验+创建"的串行化。

配额是本项目唯一会"凭一条配置就让创建失败"的闸门，口径错一位
（0 当成不限、缺失当成 0）就是线上事故，所以三态语义要逐个钉住。
"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import ParamError, QuotaExceeded
from app.services import quota_service as qs


def _user(**quota):
    return SimpleNamespace(id="u-1", username="admin", role="admin", quota=quota or None)


def _stub_count(monkeypatch, used: int):
    calls: list[tuple[str, str]] = []

    async def fake_count(user_id, kind):
        calls.append((str(user_id), kind))
        return used

    monkeypatch.setattr(qs, "count_owned", fake_count)
    return calls


def _stub_audit(monkeypatch):
    written: list[dict] = []

    async def fake_write(**fields):
        written.append(fields)

    monkeypatch.setattr(qs.AuditRepo, "write", fake_write)
    return written


# ===== 写入侧：后台提交的 quota 对象 =====
def test_normalize_drops_null_and_keeps_zero():
    """null=不限与 0=禁止是两件事：前者存不存都一样，后者必须原样保留"""
    assert qs.normalize_quota({"max_scenes": None, "max_agents": 0}) == {"max_agents": 0}
    assert qs.normalize_quota(None) == {}
    assert qs.normalize_quota({}) == {}


def test_normalize_rejects_unknown_and_invalid():
    with pytest.raises(ParamError, match="未知键"):
        qs.normalize_quota({"max_scenes": 1, "max_tickets": 3})
    with pytest.raises(ParamError, match="整数"):
        qs.normalize_quota({"max_scenes": "2"})
    # bool 是 int 子类：不排除会让 True 变成上限 1
    with pytest.raises(ParamError, match="整数"):
        qs.normalize_quota({"max_scenes": True})
    with pytest.raises(ParamError, match="取值范围"):
        qs.normalize_quota({"max_scenes": -1})
    with pytest.raises(ParamError, match="取值范围"):
        qs.normalize_quota({"max_scenes": qs.MAX_LIMIT + 1})


def test_effective_limits_missing_key_is_unlimited():
    limits = qs.effective_limits(_user(max_agents=2))
    assert limits[qs.KIND_AGENT] == 2
    assert limits[qs.KIND_SCENE] is None
    assert limits[qs.KIND_KNOWLEDGE_BASE] is None
    # 历史用户没有 quota 列/字段：一律不限，不能让登录直接崩
    assert qs.effective_limits(SimpleNamespace(id="u-2"))[qs.KIND_SCENE] is None


def test_usage_keys_cover_every_kind():
    """对外键名与内部 kind 必须一一对应，漏一个后台就会显示 0 已用"""
    assert set(qs.USAGE_KEYS) == set(qs.LIMIT_KEYS)
    assert set(qs.LIMIT_KEYS.values()) == {"max_scenes", "max_agents", "max_knowledge_bases"}


# ===== 校验：不超限就不查库 =====
@pytest.mark.asyncio
async def test_unlimited_skips_count(monkeypatch):
    calls = _stub_count(monkeypatch, 99)
    await qs.ensure_can_create(_user(), qs.KIND_SCENE)
    assert calls == []


@pytest.mark.asyncio
async def test_below_limit_passes(monkeypatch):
    calls = _stub_count(monkeypatch, 1)
    await qs.ensure_can_create(_user(max_scenes=2), qs.KIND_SCENE)
    assert calls == [("u-1", qs.KIND_SCENE)]


@pytest.mark.asyncio
async def test_reaching_limit_is_rejected_with_audit(monkeypatch):
    _stub_count(monkeypatch, 2)
    written = _stub_audit(monkeypatch)

    with pytest.raises(QuotaExceeded) as exc:
        await qs.ensure_can_create(_user(max_scenes=2), qs.KIND_SCENE)

    assert exc.value.code == 3006
    assert exc.value.http_status == 403
    assert "场景创建数量已达上限（2/2）" in str(exc.value)
    # 被挡住的创建要能追溯：谁、哪类资源、当时的上限与已用
    assert written[0]["action"] == "quota.reject"
    assert written[0]["payload"] == {"limit": 2, "used": 2}


@pytest.mark.asyncio
async def test_zero_means_forbidden(monkeypatch):
    """0 = 禁止创建：连"一个都没有"也不放行"""
    _stub_count(monkeypatch, 0)
    _stub_audit(monkeypatch)

    with pytest.raises(QuotaExceeded):
        await qs.ensure_can_create(_user(max_knowledge_bases=0), qs.KIND_KNOWLEDGE_BASE)


@pytest.mark.asyncio
async def test_create_guard_yields_after_check(monkeypatch):
    _stub_count(monkeypatch, 0)
    entered = False
    async with qs.create_guard(_user(max_agents=1), qs.KIND_AGENT):
        entered = True
    assert entered is True


@pytest.mark.asyncio
async def test_create_guard_blocks_before_body(monkeypatch):
    _stub_count(monkeypatch, 1)
    _stub_audit(monkeypatch)
    entered = False

    with pytest.raises(QuotaExceeded):
        async with qs.create_guard(_user(max_agents=1), qs.KIND_AGENT):
            entered = True
    # 校验失败时创建语句一行都不能执行
    assert entered is False


def test_unknown_kind_is_param_error():
    with pytest.raises(ParamError, match="未知的配额类型"):
        qs._count_stmt("ticket", __import__("uuid").uuid4())
