"""登录人资源配额：场景 / Agent / 知识库的创建上限。

语义（三处一致：这里、``staff_users.quota`` 列注释、后台表单）：

| 值            | 含义       |
|---------------|------------|
| 键缺失 / null | 不限       |
| 0             | 禁止创建   |
| > 0           | 上限       |

为什么用 ``created_by`` 计数而不是另建关系表：
- 资源删除后行就没了，额度自动释放，不需要维护第二份账；
- ``created_by IS NULL`` 天然区分「系统资源」（默认场景、seed 的代码 Agent、
  初始化知识库），它们不计入任何人的配额，也不需要额外标记。

并发：进程内按 ``(user_id, kind)`` 的 ``asyncio.Lock`` 串行化「校验 + 创建」，
多进程部署仍存在极小超额窗口——后台创建是低频人工操作，接受这个窗口，
不为它改仓储的事务边界。
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.exceptions import ParamError, QuotaExceeded
from app.models.entities import AgentMeta, KnowledgeBase, Scene
from app.models.repositories import AuditRepo

logger = logging.getLogger(__name__)

KIND_SCENE = "scene"
KIND_AGENT = "agent"
KIND_KNOWLEDGE_BASE = "knowledge_base"

# kind → staff_users.quota 的键名
LIMIT_KEYS = {
    KIND_SCENE: "max_scenes",
    KIND_AGENT: "max_agents",
    KIND_KNOWLEDGE_BASE: "max_knowledge_bases",
}
KIND_LABELS = {
    KIND_SCENE: "场景",
    KIND_AGENT: "Agent",
    KIND_KNOWLEDGE_BASE: "知识库",
}
# 对外（接口/前端）的已用数量键名，用复数与表含义对齐
USAGE_KEYS = {
    KIND_SCENE: "scenes",
    KIND_AGENT: "agents",
    KIND_KNOWLEDGE_BASE: "knowledge_bases",
}
# 上限本身也要有上限：后台手滑填 1000000 等于没配
MAX_LIMIT = 1000

_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _lock(user_id: str, kind: str) -> asyncio.Lock:
    key = (str(user_id), kind)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def normalize_quota(payload: dict | None) -> dict:
    """校验后台提交的配额对象：只接受已知键，值必须是非负整数（null 表示不限）。"""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ParamError("quota 必须是对象")
    unknown = sorted(set(payload) - set(LIMIT_KEYS.values()))
    if unknown:
        raise ParamError(f"quota 包含未知键: {unknown}")
    out: dict[str, int] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ParamError(f"quota.{key} 必须是整数")
        if value < 0 or value > MAX_LIMIT:
            raise ParamError(f"quota.{key} 取值范围 0-{MAX_LIMIT}")
        out[key] = value
    return out


def effective_limits(user) -> dict[str, int | None]:
    """当前登录人的生效上限：缺失键返回 None（不限）。"""
    quota = dict(getattr(user, "quota", None) or {})
    limits: dict[str, int | None] = {}
    for kind, key in LIMIT_KEYS.items():
        raw = quota.get(key)
        limits[kind] = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else None
    return limits


def _count_stmt(kind: str, owner: uuid.UUID):
    if kind == KIND_SCENE:
        return select(func.count()).select_from(Scene).where(Scene.created_by == owner)
    if kind == KIND_AGENT:
        return select(func.count()).select_from(AgentMeta).where(AgentMeta.created_by == owner)
    if kind == KIND_KNOWLEDGE_BASE:
        return select(func.count()).select_from(KnowledgeBase).where(
            KnowledgeBase.created_by == owner
        )
    raise ParamError(f"未知的配额类型: {kind}")


async def count_owned(user_id, kind: str) -> int:
    """统计某人名下的资源数量（created_by 为空的不属于任何人）。"""
    owner = uuid.UUID(str(user_id))
    async with SessionLocal() as db:
        return int((await db.execute(_count_stmt(kind, owner))).scalar_one())


async def staff_usage(user_id) -> dict[str, int]:
    """单人的三种资源已用数量（单个人详情用；列表请用 ``usage_by_owner``）。"""
    return {kind: await count_owned(user_id, kind) for kind in LIMIT_KEYS}


async def usage_by_owner(kind: str) -> dict[str, int]:
    """按 created_by 一次分组统计（人员列表禁止“每人三次 count”的 N+1）。"""
    if kind == KIND_SCENE:
        stmt = select(Scene.created_by, func.count()).group_by(Scene.created_by)
    elif kind == KIND_AGENT:
        stmt = select(AgentMeta.created_by, func.count()).group_by(AgentMeta.created_by)
    elif kind == KIND_KNOWLEDGE_BASE:
        stmt = (
            select(KnowledgeBase.created_by, func.count())
            .group_by(KnowledgeBase.created_by)
        )
    else:
        raise ParamError(f"未知的配额类型: {kind}")
    async with SessionLocal() as db:
        rows = (await db.execute(stmt)).all()
    # created_by 为空的「系统资源」不属于任何人，直接丢弃
    return {str(owner): int(count) for owner, count in rows if owner is not None}


async def ensure_can_create(user, kind: str) -> None:
    """超限抛 QuotaExceeded(3006)，并写一条审计便于追溯是谁被挡住的。"""
    limit = effective_limits(user).get(kind)
    if limit is None:
        return
    used = await count_owned(user.id, kind)
    if used < limit:
        return
    label = KIND_LABELS[kind]
    await AuditRepo.write(
        action="quota.reject",
        user_id=str(user.id),
        target_type=kind,
        payload={"limit": limit, "used": used},
    )
    raise QuotaExceeded(
        f"{label}创建数量已达上限（{used}/{limit}），"
        f"请删除不再使用的{label}或联系管理员调整配额"
    )


@asynccontextmanager
async def create_guard(user, kind: str):
    """把「校验 + 创建」放进同一把进程内锁，避免连续点击创建出第 N+1 个。"""
    async with _lock(user.id, kind):
        await ensure_can_create(user, kind)
        yield
