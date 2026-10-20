"""初始化向导与启动自检的纯函数测试（不连库）。"""
import os
import stat
import tempfile
from pathlib import Path

import pytest

from app.core.config import is_weak_jwt_secret
from app.core.selfcheck import _host_of
from app.setup import (
    MIN_PASSWORD_LEN,
    ensure_jwt_secret,
    read_env_value,
    set_env_value,
    validate_credentials,
)


def test_set_env_value_replaces_existing():
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text(
            "APP_ENV=development\nJWT_SECRET=old\nLLM_MODEL_ID=deepseek\n", encoding="utf-8"
        )

        set_env_value(env, "JWT_SECRET", "new-secret")

        lines = env.read_text(encoding="utf-8").splitlines()
        assert "JWT_SECRET=new-secret" in lines
        assert "JWT_SECRET=old" not in lines
        # 其余配置原样保留
        assert "APP_ENV=development" in lines
        assert "LLM_MODEL_ID=deepseek" in lines


def test_set_env_value_appends_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("APP_ENV=development\n", encoding="utf-8")

        set_env_value(env, "JWT_SECRET", "generated")

        text = env.read_text(encoding="utf-8")
        assert text.endswith("JWT_SECRET=generated\n")
        assert "APP_ENV=development" in text


def test_set_env_value_collapses_duplicate_keys():
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("JWT_SECRET=first\nOTHER=1\nJWT_SECRET=second\n", encoding="utf-8")

        set_env_value(env, "JWT_SECRET", "new-secret")

        lines = env.read_text(encoding="utf-8").splitlines()
        assert [line for line in lines if line.startswith("JWT_SECRET=")] == ["JWT_SECRET=new-secret"]
        assert "OTHER=1" in lines


def test_set_env_value_handles_export_and_spaces():
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("export JWT_SECRET = old\n", encoding="utf-8")

        set_env_value(env, "JWT_SECRET", "new-secret")

        assert read_env_value(env, "JWT_SECRET") == "new-secret"
        assert "export JWT_SECRET" not in env.read_text(encoding="utf-8")


def test_read_env_value_last_wins_and_strips_quotes():
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("JWT_SECRET=first\nJWT_SECRET='second'\n", encoding="utf-8")

        assert read_env_value(env, "JWT_SECRET") == "second"
        assert read_env_value(Path(tmp) / "missing.env", "JWT_SECRET") is None


def test_ensure_jwt_secret_uses_given_env_file_only():
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / "root.env"
        env.write_text("APP_ENV=development\nJWT_SECRET=change_me_in_prod\n", encoding="utf-8")

        first = ensure_jwt_secret(env)
        secret = read_env_value(env, "JWT_SECRET")
        assert secret is not None and len(secret) >= 32
        assert "已生成" in first

        second = ensure_jwt_secret(env)
        assert read_env_value(env, "JWT_SECRET") == secret
        assert "未改动" in second


def test_set_env_value_restricts_permissions_posix():
    if os.name != "posix":
        pytest.skip("Windows 不支持 POSIX 权限位")
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("APP_ENV=development\n", encoding="utf-8")

        set_env_value(env, "JWT_SECRET", "generated")

        assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_validate_credentials_accepts_admin():
    validate_credentials("admin", "Admin@123")


@pytest.mark.parametrize("username", ["ab", "有中文", "a b", "x" * 65])
def test_validate_credentials_rejects_bad_username(username):
    with pytest.raises(ValueError):
        validate_credentials(username, "Admin@123")


def test_validate_credentials_rejects_short_password():
    with pytest.raises(ValueError):
        validate_credentials("admin", "x" * (MIN_PASSWORD_LEN - 1))


def test_is_weak_jwt_secret():
    assert is_weak_jwt_secret("change_me_in_prod")
    assert is_weak_jwt_secret("short")
    assert not is_weak_jwt_secret("k" * 32)


def test_host_of_masks_credentials():
    assert _host_of("postgresql+asyncpg://cs_user:cs_pass@postgres:5432/cs_db") == "postgres:5432"
    assert _host_of("http://qdrant:6333") == "qdrant:6333"
    assert _host_of("") == "-"
