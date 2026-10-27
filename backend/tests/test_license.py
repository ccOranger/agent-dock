"""License 校验与功能位：降级、过期、篡改、指纹、依赖与接口。

全部用例用临时生成的 Ed25519 密钥对签名，不依赖私有签发工具，
也不读取真实 `./license.json`（每个用例都把 LICENSE_FILE 指到 tmp_path）。
"""
import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import current_user, require_feature
from app.api.v1 import admin_license as al
from app.core import license as lic
from app.core.config import settings


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.b64encode(raw).decode()


def _document(private: Ed25519PrivateKey, **overrides) -> dict:
    payload = {
        "v": 1,
        "customer": "某某科技有限公司",
        "edition": "enterprise",
        "features": ["rbac", "audit"],
        "issued_at": "2026-11-01T00:00:00Z",
        "expires_at": (datetime.now(UTC) + timedelta(days=365)).isoformat().replace("+00:00", "Z"),
        "deployment_id": "dep_test",
        "contract_ref": "HT-2026-0117",
        "mode": "on_prem",
    }
    payload.update(overrides)
    signature = private.sign(lic.canonical_payload(payload))
    return {**payload, "signature": base64.b64encode(signature).decode()}


@pytest.fixture(autouse=True)
def _reset_cache():
    """进程内缓存会把上一个用例的结论带过来，每个用例前后都清掉。"""
    lic._cached = None
    yield
    lic._cached = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    private, public = _keypair()
    path = tmp_path / "license.json"
    monkeypatch.setattr(settings, "license_file", str(path))
    monkeypatch.setattr(settings, "license_public_key", public)
    monkeypatch.setattr(settings, "deployment_id", "dep_test")
    return SimpleNamespace(private=private, public=public, path=path)


def _write(path, document: dict) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def test_missing_file_falls_back_to_community(env) -> None:
    status = lic.reload_license()

    assert status.edition == "community"
    assert status.valid is False and status.expired is False
    assert status.configured is False  # 开箱没有授权文件属正常状态，不该弹提示
    assert "未找到授权文件" in status.reason
    assert lic.has_feature("rbac") is False


def test_valid_license_unlocks_features(env) -> None:
    _write(env.path, _document(env.private))

    status = lic.reload_license()

    assert status.valid is True
    assert status.edition == "enterprise"
    assert status.configured is True
    assert status.customer == "某某科技有限公司"
    assert status.active_features == {"rbac", "audit"}
    assert status.contract_ref == "HT-2026-0117"
    assert status.deployment_bound is True
    assert status.days_left == 364  # 365 天有效期按整天向下取整
    assert lic.current_edition() == "enterprise"
    assert lic.has_feature("audit") is True
    assert lic.has_feature("sso") is False


def test_expired_license_downgrades_but_keeps_declared_features(env) -> None:
    past = (datetime.now(UTC) - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    _write(env.path, _document(env.private, expires_at=past))

    status = lic.reload_license()

    assert status.expired is True
    assert status.valid is False
    assert status.edition == "community"
    # 声明过的功能位仍保留，后台可展示“曾授权”，但不再生效
    assert status.features == {"rbac", "audit"}
    assert status.active_features == set()
    assert lic.has_feature("rbac") is False
    assert "到期" in status.reason


def test_tampered_features_break_signature(env) -> None:
    document = _document(env.private)
    document["features"] = ["rbac", "audit", "eval_pro", "white_label", "sso"]
    _write(env.path, document)

    status = lic.reload_license()

    assert status.valid is False
    assert status.edition == "community"
    assert "签名校验失败" in status.reason


def test_missing_signature_is_rejected(env) -> None:
    document = _document(env.private)
    document.pop("signature")
    _write(env.path, document)

    assert "signature" in lic.reload_license().reason


def test_deployment_mismatch_downgrades(env, monkeypatch) -> None:
    _write(env.path, _document(env.private, deployment_id="dep_someone_else"))
    monkeypatch.setattr(settings, "deployment_id", "dep_test")

    status = lic.reload_license()

    assert status.edition == "community"
    assert status.valid is False
    assert "DEPLOYMENT_ID" in status.reason


def test_empty_local_deployment_id_skips_fingerprint(env, monkeypatch) -> None:
    _write(env.path, _document(env.private, deployment_id="dep_customer"))
    monkeypatch.setattr(settings, "deployment_id", "")

    status = lic.reload_license()

    assert status.valid is True and status.edition == "enterprise"
    assert status.deployment_bound is False


def test_unknown_features_are_filtered(env) -> None:
    _write(env.path, _document(env.private, features=["rbac", "max_scenes", "eval_pro"]))

    status = lic.reload_license()

    assert status.active_features == {"rbac", "eval_pro"}
    assert status.unknown_features == ("max_scenes",)


def test_missing_public_key_downgrades(env, monkeypatch) -> None:
    _write(env.path, _document(env.private))
    monkeypatch.setattr(settings, "license_public_key", "")

    status = lic.reload_license()

    assert status.edition == "community"
    assert "LICENSE_PUBLIC_KEY" in status.reason


def test_unsupported_version_is_rejected(env) -> None:
    _write(env.path, _document(env.private, v=2))

    assert "不支持的授权文件版本" in lic.reload_license().reason


def test_reload_picks_up_replaced_file(env) -> None:
    _write(env.path, _document(env.private))
    assert lic.reload_license().valid is True

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    _write(env.path, _document(env.private, expires_at=past))

    assert lic.reload_license().expired is True


def test_require_feature_denies_on_community(env) -> None:
    app = FastAPI()

    @app.get("/probe")
    async def probe(user=Depends(require_feature("audit"))):
        return {"ok": True}

    app.dependency_overrides[current_user] = lambda: SimpleNamespace(
        id="u-1", role="admin", is_active=True
    )
    resp = TestClient(app).get("/probe")

    assert resp.status_code == 403
    assert "审计日志" in resp.json()["detail"]["message"]


def test_require_feature_allows_when_licensed(env) -> None:
    _write(env.path, _document(env.private))
    lic.reload_license()
    app = FastAPI()

    @app.get("/probe")
    async def probe(user=Depends(require_feature("audit"))):
        return {"ok": True}

    app.dependency_overrides[current_user] = lambda: SimpleNamespace(
        id="u-1", role="admin", is_active=True
    )

    assert TestClient(app).get("/probe").status_code == 200


@pytest.fixture
def client(make_test_app, env):
    app = make_test_app([al.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def test_license_endpoint_reports_status_and_catalog(client, env) -> None:
    _write(env.path, _document(env.private))
    lic.reload_license()

    resp = client.get("/admin/license")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["edition"] == "enterprise"
    assert data["edition_label"] == "企业版"
    assert data["features"] == ["audit", "rbac"]
    assert data["expiring_soon"] is False
    # 功能位目录由后端下发，含全部 5 项
    assert set(data["feature_catalog"]) == {"rbac", "audit", "white_label", "eval_pro", "sso"}
    assert data["community_scope"]
    assert data["customer"] == "某某科技有限公司"


def test_license_endpoint_marks_degraded(client, env, monkeypatch) -> None:
    monkeypatch.setattr(settings, "license_public_key", "")

    data = client.get("/admin/license").json()["data"]

    assert data["edition"] == "community"
    assert data["valid"] is False
    assert data["reason"]
    assert data["feature_catalog"]["rbac"]["label"] == "精细化权限"


def test_license_reload_endpoint_rereads_file(client, env) -> None:
    assert client.get("/admin/license").json()["data"]["edition"] == "community"

    _write(env.path, _document(env.private))
    resp = client.post("/admin/license/reload")

    assert resp.status_code == 200
    assert resp.json()["data"]["edition"] == "enterprise"


def test_license_endpoint_rejects_agent(make_test_app, env) -> None:
    app = make_test_app([al.router], prefix="")
    resp = TestClient(make_test_app.as_role(app, "agent")).get("/admin/license")

    assert resp.status_code == 403
