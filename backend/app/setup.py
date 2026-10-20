"""初始化向导：`python -m app.setup`（在 backend 目录执行）。

面向首次部署（裸机或自定义环境）：
1. 建表（与 seed_data 同源，幂等）
2. 创建管理员（已存在则保留，--reset-password 可重置）
3. 生成随机 JWT_SECRET 并写回 --env-file（以该文件为唯一判定源；已配置强密钥则不动）
4. 测试模型连通性（可选 --skip-llm-check）

Docker Compose 部署无需本向导：入口脚本会自动建表、灌种子并创建初始管理员。

用法：
    python -m app.setup                                   # 交互式
    python -m app.setup --username admin --password '...'  # 非交互
    python -m app.setup --env-file ../.env                # Docker Compose（仓库根 .env）
"""
import argparse
import asyncio
import getpass
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

from app.core.config import is_weak_jwt_secret
from app.core.selfcheck import check_model

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
MIN_PASSWORD_LEN = 8


def _unquote(raw: str) -> str:
    """去掉值两端的引号；未加引号时剔除行尾 ` # 注释`。"""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value.split(" #", 1)[0].rstrip()


def read_env_value(path: Path, key: str) -> str | None:
    """读取 env 文件中的值；重复键以最后一行为准（与 python-dotenv / Compose 一致）。"""
    if not path.exists():
        return None
    value = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) == key:
            value = _unquote(match.group(2))
    return value


def _atomic_write(path: Path, content: str) -> None:
    """同目录临时文件 + os.replace，避免截断丢配置；POSIX 下权限收紧到 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def set_env_value(path: Path, key: str, value: str) -> None:
    """把 KEY=value 原子写回 env 文件。

    兼容 `export KEY=` 与 `KEY = value`；重复键折叠为第一处并删除其余，
    避免 dotenv 取最后一行导致新值不生效。
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    out: list[str] = []
    replaced = False
    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) == key:
            if not replaced:
                out.append(f"{prefix}{value}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"{prefix}{value}")
    _atomic_write(path, "\n".join(out) + "\n")


def validate_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise ValueError("用户名需 3~64 位，仅允许字母、数字、下划线、点、短横线")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LEN} 位")


def resolve_admin(args: argparse.Namespace) -> tuple[str, str, str]:
    """返回 (username, password, display_name)；参数不全时交互补全。"""
    username = (args.username or "").strip()
    password = args.password or ""
    display_name = (args.display_name or "").strip()

    interactive = not (username and password)
    if interactive and not sys.stdin.isatty():
        raise SystemExit("非交互环境请同时提供 --username 与 --password")

    if not username:
        username = (input("管理员用户名 [admin]: ").strip() or "admin")
    if not password:
        password = getpass.getpass(f"管理员密码（至少 {MIN_PASSWORD_LEN} 位）: ")
        confirm = getpass.getpass("再次输入密码: ")
        if password != confirm:
            raise SystemExit("两次输入的密码不一致")
    if not display_name:
        display_name = "管理员"

    validate_credentials(username, password)
    return username, password, display_name


async def ensure_admin(username: str, password: str, display_name: str, reset: bool) -> str:
    """建表并创建管理员，返回可读结果。"""
    from sqlalchemy import select

    from app.core.database import engine
    from app.core.security import hash_password
    from app.models.entities import Base, StaffUser

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(engine) as db:
        row = (
            await db.execute(select(StaffUser).where(StaffUser.username == username))
        ).scalar_one_or_none()
        if row is None:
            db.add(
                StaffUser(
                    username=username,
                    password_hash=hash_password(password),
                    role="admin",
                    display_name=display_name,
                )
            )
            await db.commit()
            return f"已创建管理员：{username}"
        if reset:
            row.password_hash = hash_password(password)
            await db.commit()
            return f"已重置管理员密码：{username}"
        return f"管理员 {username} 已存在，未修改密码（如需重置加 --reset-password）"


def ensure_jwt_secret(env_path: Path) -> str:
    """以 env_path 为唯一真源：缺失或偏弱时生成随机密钥并原子写回。"""
    current = read_env_value(env_path, "JWT_SECRET")
    if current is not None and not is_weak_jwt_secret(current):
        return f"JWT_SECRET 已配置（{env_path}，强度满足要求），未改动"
    set_env_value(env_path, "JWT_SECRET", secrets.token_urlsafe(48))
    reason = "未配置" if current is None else "仍是占位/弱密钥"
    return f"JWT_SECRET {reason}，已生成随机密钥并写入 {env_path}（重启后端后生效）"


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.setup", description="初始化管理员与运行配置")
    parser.add_argument("--username", help="管理员用户名（默认 admin）")
    parser.add_argument("--password", help="管理员密码（至少 8 位）；省略则交互输入")
    parser.add_argument("--display-name", help="显示名（默认“管理员”）")
    parser.add_argument("--reset-password", action="store_true", help="账号已存在时重置密码")
    parser.add_argument("--skip-llm-check", action="store_true", help="跳过模型连通性检查")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="JWT_SECRET 的唯一真源（读取并写回；默认 backend/.env，Compose 部署传 ../.env）",
    )
    args = parser.parse_args(argv)

    print("== smart-cs 初始化向导 ==")
    username, password, display_name = resolve_admin(args)

    print(ensure_jwt_secret(Path(args.env_file)))
    print(await ensure_admin(username, password, display_name, args.reset_password))

    if args.skip_llm_check:
        print("已跳过模型连通性检查（--skip-llm-check）")
    else:
        result = await check_model()
        level = "OK" if result.startswith("OK") else "警告"
        print(f"模型连通性：{level} — {result}")

    print("\n完成。下一步：")
    print("  1) 启动后端：uvicorn app.main:app --host 0.0.0.0 --port 8000")
    print("  2) 打开 http://localhost:8000/docs 或前端 http://localhost:5173")
    print("  3) 用刚创建的管理员登录管理后台（/admin/ 或 5174）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
