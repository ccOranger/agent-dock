"""仓储层：对 ORM 的异步封装（Repo 模式）"""
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.core.security import hash_api_key
from app.models.entities import (
    AgentMeta,
    AuditLog,
    CannedScript,
    ChatEvent,
    CustomerUser,
    Document,
    Integration,
    IntegrationKey,
    IntegrationScene,
    KnowledgeBase,
    Memory,
    Message,
    RequestTrace,
    Scene,
    SceneAgent,
    Session,
    StaffUser,
    Ticket,
    ToolMeta,
    Visitor,
)


@asynccontextmanager
async def _session(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    """外部传入 db 则复用（事务由调用方控制），否则自开自管一个 session。"""
    if db is not None:
        yield db
    else:
        async with SessionLocal() as own:
            yield own


class StaffRepo:
    """后台人员（admin / agent）仓储。"""

    @staticmethod
    async def get_by_username(username: str) -> StaffUser | None:
        async with SessionLocal() as db:
            return (
                await db.execute(select(StaffUser).where(StaffUser.username == username))
            ).scalar_one_or_none()

    @staticmethod
    async def get_by_id(user_id: uuid.UUID | str) -> StaffUser | None:
        async with SessionLocal() as db:
            return await db.get(StaffUser, uuid.UUID(str(user_id)))

    @staticmethod
    async def list_all() -> list[StaffUser]:
        """全部人员（含停用），按创建时间升序：配额页需要看到历史账号。"""
        async with SessionLocal() as db:
            rows = (
                await db.execute(select(StaffUser).order_by(StaffUser.created_at))
            ).scalars().all()
            return list(rows)

    @staticmethod
    async def update(user_id: uuid.UUID | str, **fields) -> StaffUser | None:
        """局部更新；只接受实体上存在的字段（quota / role / is_active / display_name）。"""
        async with SessionLocal() as db:
            row = await db.get(StaffUser, uuid.UUID(str(user_id)))
            if row is None:
                return None
            for key, value in fields.items():
                if value is not None and hasattr(row, key):
                    setattr(row, key, value)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def set_password(user_id: uuid.UUID | str, password_hash: str) -> bool:
        """重置密码（不校验旧密码，由后台管理员操作）。"""
        async with SessionLocal() as db:
            row = await db.get(StaffUser, uuid.UUID(str(user_id)))
            if row is None:
                return False
            row.password_hash = password_hash
            await db.commit()
            return True

    @staticmethod
    async def create(username: str, password_hash: str, role: str = "agent", **kw) -> StaffUser:
        async with SessionLocal() as db:
            user = StaffUser(username=username, password_hash=password_hash, role=role, **kw)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user


class CustomerRepo:
    """客户账号仓储。"""

    @staticmethod
    async def get_by_username(username: str) -> CustomerUser | None:
        async with SessionLocal() as db:
            return (
                await db.execute(select(CustomerUser).where(CustomerUser.username == username))
            ).scalar_one_or_none()

    @staticmethod
    async def get_by_id(user_id: uuid.UUID | str) -> CustomerUser | None:
        async with SessionLocal() as db:
            return await db.get(CustomerUser, uuid.UUID(str(user_id)))

    @staticmethod
    async def create(username: str, password_hash: str, **kw) -> CustomerUser:
        async with SessionLocal() as db:
            user = CustomerUser(username=username, password_hash=password_hash, **kw)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user


class SceneRepo:
    """场景仓储：CRUD 与 Agent 绑定。默认场景的只读保护由 API 层执行。"""

    @staticmethod
    async def get_by_code(code: str) -> Scene | None:
        async with SessionLocal() as db:
            return (
                await db.execute(select(Scene).where(Scene.code == code))
            ).scalar_one_or_none()

    @staticmethod
    async def get(scene_id: uuid.UUID | str) -> Scene | None:
        async with SessionLocal() as db:
            return await db.get(Scene, uuid.UUID(str(scene_id)))

    @staticmethod
    async def get_default() -> Scene | None:
        async with SessionLocal() as db:
            return (
                await db.execute(select(Scene).where(Scene.is_default.is_(True)))
            ).scalar_one_or_none()

    @staticmethod
    async def existing_ids(scene_ids: list[uuid.UUID]) -> set[str]:
        """批量校验场景 id 是否存在（接入方授权关系保存前用）。"""
        if not scene_ids:
            return set()
        async with SessionLocal() as db:
            rows = (
                await db.execute(select(Scene.id).where(Scene.id.in_(scene_ids)))
            ).scalars().all()
        return {str(x) for x in rows}

    @staticmethod
    async def list_all() -> list[Scene]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(Scene).order_by(Scene.is_default.desc(), Scene.created_at)
                )
            ).scalars().all()
            return list(rows)

    @staticmethod
    async def list_with_agent_counts() -> list[dict]:
        """后台列表：带启用中的绑定 Agent 数，避免逐行查询造成 N+1。"""
        enabled_agents = func.count(SceneAgent.agent_id).filter(SceneAgent.enabled.is_(True))
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(Scene, enabled_agents.label("agent_count"))
                    .outerjoin(SceneAgent, SceneAgent.scene_id == Scene.id)
                    .group_by(Scene.id)
                    .order_by(Scene.is_default.desc(), Scene.created_at)
                )
            ).all()
            return [SceneRepo.to_dict(scene, count) for scene, count in rows]

    @staticmethod
    async def get_bound_agent_ids(scene_id: uuid.UUID | str) -> list[str]:
        """后台编辑用：含已停用的绑定，按候选顺序返回。"""
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(SceneAgent.agent_id)
                    .where(SceneAgent.scene_id == uuid.UUID(str(scene_id)))
                    .order_by(SceneAgent.sort_order)
                )
            ).scalars().all()
            return [str(x) for x in rows]

    @staticmethod
    def to_dict(scene: Scene, agent_count: int = 0) -> dict:
        return {
            "id": str(scene.id),
            "code": scene.code,
            "display_name": scene.display_name,
            "description": scene.description,
            "status": scene.status,
            "is_default": bool(scene.is_default),
            "router_mode": scene.router_mode,
            "router_model": scene.router_model,
            "router_confidence_threshold": scene.router_confidence_threshold,
            "router_max_intents": scene.router_max_intents,
            "fallback_agent_id": str(scene.fallback_agent_id) if scene.fallback_agent_id else None,
            "fallback_text": scene.fallback_text,
            "extra_router_prompt": scene.extra_router_prompt,
            "runtime_config": dict(getattr(scene, "runtime_config", None) or {}),
            "publish_key": scene.publish_key,
            "allowed_origins": list(scene.allowed_origins or []),
            "widget_config": dict(getattr(scene, "widget_config", None) or {}),
            "agent_count": int(agent_count or 0),
            "created_at": scene.created_at.isoformat() if scene.created_at else None,
            "updated_at": scene.updated_at.isoformat() if scene.updated_at else None,
        }

    @staticmethod
    async def list_bound_agents(scene_id: uuid.UUID | str) -> list[dict]:
        """本场景启用中的 Agent（含全局代码 Agent），sort_order 小者优先。

        返回字段直接作为路由档案的候选输入：routing_* 决定它在路由提示词里
        能拿到多少表达机会，后台看到的候选与运行时注入的候选完全同源。
        """
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(AgentMeta, SceneAgent)
                    .join(SceneAgent, SceneAgent.agent_id == AgentMeta.id)
                    .where(
                        SceneAgent.scene_id == uuid.UUID(str(scene_id)),
                        SceneAgent.enabled.is_(True),
                        AgentMeta.enabled.is_(True),
                    )
                    .order_by(SceneAgent.sort_order, AgentMeta.created_at)
                )
            ).all()
            return [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "display_name": agent.display_name or agent.name,
                    "description": agent.description,
                    "source": agent.source,
                    "scene_id": str(agent.scene_id) if agent.scene_id else None,
                    "routing_keywords": list(agent.routing_keywords or []),
                    "routing_examples": list(agent.routing_examples or []),
                    "sort_order": link.sort_order,
                }
                for agent, link in rows
            ]

    @staticmethod
    async def list_agents_for_admin(scene_id: uuid.UUID | str) -> list[dict]:
        """后台绑定弹窗用：可绑定 Agent = 全局 Agent + 本场景专属 Agent。

        其它场景专属的 Agent 不展示，避免把 A 场景的 Agent 绑到 B 场景造成跨场景读取。
        """
        sid = uuid.UUID(str(scene_id))
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(AgentMeta, SceneAgent)
                    .outerjoin(
                        SceneAgent,
                        (SceneAgent.agent_id == AgentMeta.id) & (SceneAgent.scene_id == sid),
                    )
                    .where(
                        (AgentMeta.scene_id.is_(None)) | (AgentMeta.scene_id == sid)
                    )
                    .order_by(AgentMeta.source, AgentMeta.sort_order, AgentMeta.created_at)
                )
            ).all()
            return [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "display_name": agent.display_name or agent.name,
                    "source": agent.source,
                    "enabled": agent.enabled,
                    "bound": link is not None,
                    "scene_enabled": bool(link.enabled) if link else False,
                    "sort_order": link.sort_order if link else 10_000,
                }
                for agent, link in rows
            ]

    @staticmethod
    async def create(code: str, display_name: str, description: str | None = None, **fields) -> Scene:
        async with SessionLocal() as db:
            row = Scene(code=code, display_name=display_name, description=description, **fields)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def update(scene_id: uuid.UUID | str, **fields) -> Scene | None:
        async with SessionLocal() as db:
            row = await db.get(Scene, uuid.UUID(str(scene_id)))
            if row is None:
                return None
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def delete(scene_id: uuid.UUID | str) -> bool:
        async with SessionLocal() as db:
            row = await db.get(Scene, uuid.UUID(str(scene_id)))
            if row is None:
                return False
            await db.delete(row)
            await db.commit()
            return True

    @staticmethod
    async def set_agents(
        scene_id: uuid.UUID | str,
        agent_ids: list[str],
        db: AsyncSession | None = None,
    ) -> None:
        """全量替换绑定：顺序即路由候选顺序；被移除的绑定直接删除。"""
        sid = uuid.UUID(str(scene_id))
        # 保序去重，避免同一 Agent 重复插入触发主键冲突
        wanted = list(dict.fromkeys(agent_ids))
        own = db is None
        async with _session(db) as s:
            existing = {
                link.agent_id: link
                for link in (
                    await s.execute(select(SceneAgent).where(SceneAgent.scene_id == sid))
                ).scalars()
            }
            keep = {uuid.UUID(str(aid)) for aid in wanted}
            for agent_id, link in existing.items():
                if agent_id not in keep:
                    await s.delete(link)
            for order, raw_id in enumerate(wanted):
                agent_id = uuid.UUID(str(raw_id))
                link = existing.get(agent_id)
                if link is None:
                    s.add(
                        SceneAgent(
                            scene_id=sid, agent_id=agent_id, enabled=True, sort_order=order
                        )
                    )
                else:
                    link.enabled = True
                    link.sort_order = order
            # 外部传 db 时事务由调用方提交；自开 session 必须在这里落盘，
            # 否则退出上下文直接回滚（绑定看起来保存成功但库里没有）
            if own:
                await s.commit()


class VisitorRepo:
    """访客：浏览器匿名按场景复用；服务端接入再按接入方隔离命名空间。"""

    @staticmethod
    async def get_or_create(
        scene_id: uuid.UUID | str,
        external_key: str | None = None,
        integration_id: uuid.UUID | str | None = None,
    ) -> Visitor:
        sid = uuid.UUID(str(scene_id))
        iid = uuid.UUID(str(integration_id)) if integration_id else None
        if not external_key:
            async with SessionLocal() as db:
                row = Visitor(scene_id=sid, integration_id=iid, last_seen_at=func.now())
                db.add(row)
                await db.commit()
                await db.refresh(row)
                return row
        async with SessionLocal() as db:
            # 匿名与服务端接入走两套部分唯一索引：NULL 参与的唯一约束
            # 在 PostgreSQL 里默认互不相等，必须按 integration_id 是否为空选索引。
            if iid is None:
                stmt = (
                    pg_insert(Visitor)
                    .values(scene_id=sid, external_key=external_key, last_seen_at=func.now())
                    .on_conflict_do_update(
                        index_elements=["scene_id", "external_key"],
                        index_where=Visitor.integration_id.is_(None),
                        set_={"last_seen_at": func.now()},
                    )
                    .returning(Visitor)
                )
            else:
                stmt = (
                    pg_insert(Visitor)
                    .values(
                        scene_id=sid,
                        integration_id=iid,
                        external_key=external_key,
                        last_seen_at=func.now(),
                    )
                    .on_conflict_do_update(
                        index_elements=["integration_id", "scene_id", "external_key"],
                        index_where=Visitor.integration_id.is_not(None),
                        set_={"last_seen_at": func.now()},
                    )
                    .returning(Visitor)
                )
            row = (await db.execute(stmt)).scalar_one()
            await db.commit()
            return row


class IntegrationRepo:
    """接入方：API Key 归属、场景授权与命名空间查询。"""

    @staticmethod
    def new_public_code() -> str:
        """生成可公开的嵌入标识；64 bit 随机数，碰撞可忽略。"""
        return f"ic_{secrets.token_hex(8)}"

    @staticmethod
    def to_dict(integration: Integration, key_count: int = 0, scenes: list[dict] | None = None) -> dict:
        return {
            "id": str(integration.id),
            "name": integration.name,
            "public_code": integration.public_code,
            "description": integration.description,
            "status": integration.status,
            "key_count": int(key_count or 0),
            "scenes": scenes or [],
            "created_at": integration.created_at.isoformat() if integration.created_at else None,
            "updated_at": integration.updated_at.isoformat() if integration.updated_at else None,
        }

    @staticmethod
    async def list_all() -> list[dict]:
        """后台列表：一次查完接入方、Key 数、授权场景，避免逐行 N+1。"""
        async with SessionLocal() as db:
            rows = (
                await db.execute(select(Integration).order_by(Integration.created_at))
            ).scalars().all()
            key_counts = dict(
                (
                    await db.execute(
                        select(IntegrationKey.integration_id, func.count())
                        .group_by(IntegrationKey.integration_id)
                    )
                ).all()
            )
            links = (
                await db.execute(
                    select(IntegrationScene, Scene)
                    .join(Scene, Scene.id == IntegrationScene.scene_id)
                    .order_by(Scene.is_default.desc(), Scene.created_at)
                )
            ).all()
        scenes: dict[uuid.UUID, list[dict]] = {}
        for link, scene in links:
            scenes.setdefault(link.integration_id, []).append(
                {"id": str(scene.id), "code": scene.code, "display_name": scene.display_name}
            )
        return [
            IntegrationRepo.to_dict(row, key_counts.get(row.id, 0), scenes.get(row.id, []))
            for row in rows
        ]

    @staticmethod
    async def get(integration_id: uuid.UUID | str) -> Integration | None:
        async with SessionLocal() as db:
            return await db.get(Integration, uuid.UUID(str(integration_id)))

    @staticmethod
    async def detail(integration_id: uuid.UUID | str) -> dict | None:
        iid = uuid.UUID(str(integration_id))
        async with SessionLocal() as db:
            row = await db.get(Integration, iid)
            if row is None:
                return None
            keys = (
                await db.execute(
                    select(IntegrationKey)
                    .where(IntegrationKey.integration_id == iid)
                    .order_by(IntegrationKey.created_at)
                )
            ).scalars().all()
            scenes = (
                await db.execute(
                    select(Scene)
                    .join(IntegrationScene, IntegrationScene.scene_id == Scene.id)
                    .where(IntegrationScene.integration_id == iid)
                    .order_by(Scene.is_default.desc(), Scene.created_at)
                )
            ).scalars().all()
        data = IntegrationRepo.to_dict(
            row,
            len(keys),
            [
                {"id": str(s.id), "code": s.code, "display_name": s.display_name}
                for s in scenes
            ],
        )
        data["keys"] = [
            {
                "id": str(k.id),
                "hint": k.hint,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in keys
        ]
        return data

    @staticmethod
    async def create(
        name: str,
        description: str | None = None,
        scene_ids: list[str] | None = None,
    ) -> Integration:
        async with SessionLocal() as db:
            row = Integration(
                name=name,
                description=description,
                public_code=IntegrationRepo.new_public_code(),
            )
            db.add(row)
            await db.flush()
            for raw_id in scene_ids or []:
                db.add(
                    IntegrationScene(
                        integration_id=row.id, scene_id=uuid.UUID(str(raw_id))
                    )
                )
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def grant_scene_to_all(scene_id: uuid.UUID | str) -> int:
        """单租户降级：把新场景授权给所有启用中的接入方。

        平台内部所有调用方都应能访问新场景；不自动授权会导致已有 Key
        调用新场景直接 403。返回新增授权条数。
        """
        sid = uuid.UUID(str(scene_id))
        async with SessionLocal() as db:
            integration_ids = (
                await db.execute(select(Integration.id).where(Integration.status == "active"))
            ).scalars().all()
            added = 0
            for iid in integration_ids:
                if await db.get(IntegrationScene, (iid, sid)) is None:
                    db.add(IntegrationScene(integration_id=iid, scene_id=sid))
                    added += 1
            await db.commit()
            return added

    @staticmethod
    async def update(
        integration_id: uuid.UUID | str,
        scene_ids: list[str] | None = None,
        **fields,
    ) -> Integration | None:
        iid = uuid.UUID(str(integration_id))
        async with SessionLocal() as db:
            row = await db.get(Integration, iid)
            if row is None:
                return None
            for key, value in fields.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            if scene_ids is not None:
                wanted = {uuid.UUID(str(x)) for x in scene_ids}
                existing = {
                    link.scene_id: link
                    for link in (
                        await db.execute(
                            select(IntegrationScene).where(IntegrationScene.integration_id == iid)
                        )
                    ).scalars()
                }
                for scene_id, link in existing.items():
                    if scene_id not in wanted:
                        await db.delete(link)
                for scene_id in wanted - set(existing):
                    db.add(IntegrationScene(integration_id=iid, scene_id=scene_id))
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def delete(integration_id: uuid.UUID | str) -> bool:
        async with SessionLocal() as db:
            row = await db.get(Integration, uuid.UUID(str(integration_id)))
            if row is None:
                return False
            await db.delete(row)
            await db.commit()
            return True

    @staticmethod
    async def has_visitors(integration_id: uuid.UUID | str) -> bool:
        iid = uuid.UUID(str(integration_id))
        async with SessionLocal() as db:
            row = await db.execute(
                select(Visitor.id).where(Visitor.integration_id == iid).limit(1)
            )
            return row.first() is not None

    @staticmethod
    async def list_keys(integration_id: uuid.UUID | str) -> list[dict]:
        iid = uuid.UUID(str(integration_id))
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(IntegrationKey)
                    .where(IntegrationKey.integration_id == iid)
                    .order_by(IntegrationKey.created_at)
                )
            ).scalars().all()
        return [
            {
                "id": str(row.id),
                "hint": row.hint,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    @staticmethod
    async def create_key(integration_id: uuid.UUID | str, raw: str, hint: str) -> IntegrationKey:
        async with SessionLocal() as db:
            row = IntegrationKey(
                integration_id=uuid.UUID(str(integration_id)),
                key_hash=hash_api_key(raw),
                hint=hint,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def revoke_key(integration_id: uuid.UUID | str, key_id: uuid.UUID | str) -> bool:
        iid = uuid.UUID(str(integration_id))
        kid = uuid.UUID(str(key_id))
        async with SessionLocal() as db:
            row = await db.get(IntegrationKey, kid)
            if row is None or row.integration_id != iid:
                return False
            await db.delete(row)
            await db.commit()
            return True

    @staticmethod
    async def resolve_key(raw: str) -> dict | None:
        """按明文 Key 反查启用中的接入方及其已授权场景。

        返回 None 表示 Key 不存在或接入方已停用；命中只代表 Key 有效，
        是否允许访问某个场景由 authorize_scene_access 再校验。
        """
        digest = hash_api_key(raw)
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(Integration)
                    .join(IntegrationKey, IntegrationKey.integration_id == Integration.id)
                    .where(
                        IntegrationKey.key_hash == digest,
                        Integration.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            scene_ids = (
                await db.execute(
                    select(IntegrationScene.scene_id).where(
                        IntegrationScene.integration_id == row.id
                    )
                )
            ).scalars().all()
        return {
            "id": str(row.id),
            "name": row.name,
            "public_code": row.public_code,
            "via": "api_key",
            "scene_ids": {str(x) for x in scene_ids},
        }

    @staticmethod
    async def resolve_public_code(raw: str) -> dict | None:
        """按公开标识反查启用中的接入方及其已授权场景。

        匿名嵌入没有 API Key，只能用公开标识确定归属；它不承担鉴权强度，
        只用于把访客落进正确的接入方命名空间，场景授权仍在这里校验。
        """
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(Integration).where(
                        Integration.public_code == raw,
                        Integration.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            scene_ids = (
                await db.execute(
                    select(IntegrationScene.scene_id).where(
                        IntegrationScene.integration_id == row.id
                    )
                )
            ).scalars().all()
        return {
            "id": str(row.id),
            "name": row.name,
            "public_code": row.public_code,
            "via": "public_code",
            "scene_ids": {str(x) for x in scene_ids},
        }


class SessionRepo:
    @staticmethod
    async def get_or_create(
        user_id: uuid.UUID | str,
        session_id: uuid.UUID | str | None,
        db: AsyncSession | None = None,
    ) -> Session:
        own = db is None
        async with _session(db) as s:
            if session_id:
                sess = await s.get(Session, uuid.UUID(str(session_id)))
                if sess and str(sess.user_id) == str(user_id):
                    return sess
            sess = Session(user_id=uuid.UUID(str(user_id)))
            s.add(sess)
            await s.flush()
            if own:
                await s.commit()
                await s.refresh(sess)
            return sess

    @staticmethod
    async def get_or_create_for_actor(
        actor,
        scene_id: uuid.UUID | str,
        session_id: uuid.UUID | str | None,
        db: AsyncSession | None = None,
    ) -> Session:
        """按 actor + 场景取会话；归属不匹配时视为无效 session_id，另起新会话。

        不匹配不报错是有意为之：既不泄露“该 session 存在但不属于你”，
        也与原实现「session 不属于当前用户就新建」保持一致。
        """
        own = db is None
        async with _session(db) as s:
            if session_id:
                sess = await s.get(Session, uuid.UUID(str(session_id)))
                if sess is not None and SessionRepo._belongs(sess, actor, scene_id):
                    return sess
            values: dict = {"scene_id": uuid.UUID(str(scene_id))}
            if actor.is_visitor:
                values["visitor_id"] = uuid.UUID(str(actor.id))
            else:
                values["user_id"] = uuid.UUID(str(actor.id))
            sess = Session(**values)
            s.add(sess)
            await s.flush()
            if own:
                await s.commit()
                await s.refresh(sess)
            return sess

    @staticmethod
    def _belongs(sess: Session, actor, scene_id: uuid.UUID | str) -> bool:
        if str(sess.scene_id) != str(scene_id):
            return False
        if actor.is_visitor:
            return sess.visitor_id is not None and str(sess.visitor_id) == str(actor.id)
        return sess.user_id is not None and str(sess.user_id) == str(actor.id)

    @staticmethod
    async def create(
        user_id: uuid.UUID | str, title: str = "新对话", scene_id: uuid.UUID | str | None = None
    ) -> Session:
        async with SessionLocal() as db:
            values: dict = {"user_id": uuid.UUID(str(user_id)), "title": title}
            if scene_id:
                values["scene_id"] = uuid.UUID(str(scene_id))
            sess = Session(**values)
            db.add(sess)
            await db.commit()
            await db.refresh(sess)
            return sess

    @staticmethod
    async def get(session_id: uuid.UUID | str, db: AsyncSession | None = None) -> Session | None:
        async with _session(db) as s:
            return await s.get(Session, uuid.UUID(str(session_id)))

    @staticmethod
    async def list_by_user(
        user_id: uuid.UUID | str,
        page: int = 1,
        page_size: int = 20,
        scene_id: uuid.UUID | str | None = None,
    ) -> tuple[list[Session], int]:
        """客户的会话列表；给定 scene_id 时只返回该场景下的会话（场景隔离）。"""
        async with SessionLocal() as db:
            cond = [Session.user_id == uuid.UUID(str(user_id))]
            if scene_id:
                cond.append(Session.scene_id == uuid.UUID(str(scene_id)))
            total = (
                await db.execute(select(func.count()).select_from(Session).where(*cond))
            ).scalar_one()
            rows = (
                await db.execute(
                    select(Session)
                    .where(*cond)
                    .order_by(Session.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return list(rows), total

    @staticmethod
    def _apply_filters(stmt, user_id, status, last_route_to, date_from, date_to, scene_id=None):
        if user_id:
            stmt = stmt.where(Session.user_id == uuid.UUID(str(user_id)))
        if status:
            stmt = stmt.where(Session.status == status)
        if last_route_to:
            stmt = stmt.where(Session.last_route_to == last_route_to)
        if scene_id:
            stmt = stmt.where(Session.scene_id == uuid.UUID(str(scene_id)))
        if date_from:
            stmt = stmt.where(Session.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Session.created_at <= date_to)
        return stmt

    @staticmethod
    async def list_all_filtered(
        user_id: uuid.UUID | str | None = None,
        status: str | None = None,
        last_route_to: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        scene_id: uuid.UUID | str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """跨客户会话查询（管理后台）：JOIN 用户带出客户名 + 消息数。

        必须用外连接：嵌入场景的匿名访客会话 user_id 为 NULL，
        内连接会把它们整批丢掉（咨询列表只剩登录客户的会话）。
        """
        msg_count = (
            select(func.count())
            .select_from(Message)
            .where(Message.session_id == Session.id)
            .correlate(Session)
            .scalar_subquery()
            .label("message_count")
        )
        async with SessionLocal() as db:
            base = select(Session, CustomerUser.username, CustomerUser.display_name).outerjoin(
                CustomerUser, CustomerUser.id == Session.user_id
            )
            count_base = select(func.count()).select_from(Session)
            base = SessionRepo._apply_filters(
                base, user_id, status, last_route_to, date_from, date_to, scene_id
            )
            count_base = SessionRepo._apply_filters(
                count_base, user_id, status, last_route_to, date_from, date_to, scene_id
            )
            total = (await db.execute(count_base)).scalar_one()
            rows = (
                await db.execute(
                    base.add_columns(msg_count)
                    .order_by(Session.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            result = [
                {
                    "id": str(s.id),
                    "title": s.title,
                    "status": s.status,
                    "last_route_to": s.last_route_to,
                    "scene_id": str(s.scene_id),
                    "user_id": str(s.user_id) if s.user_id else None,
                    "visitor_id": str(s.visitor_id) if s.visitor_id else None,
                    "username": username,
                    "display_name": display_name,
                    "message_count": message_count,
                    "created_at": s.created_at.isoformat(),
                    "updated_at": s.updated_at.isoformat(),
                }
                for (s, username, display_name, message_count) in rows
            ]
            return result, total

    @staticmethod
    async def export_rows(
        user_id: uuid.UUID | str | None = None,
        status: str | None = None,
        last_route_to: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        scene_id: uuid.UUID | str | None = None,
        max_rows: int = 10000,
    ) -> list[dict]:
        """导出用：返回满足过滤条件的会话（含首条客户问句摘要）；同样外连接保留匿名访客会话。"""
        async with SessionLocal() as db:
            stmt = select(
                Session, CustomerUser.username, CustomerUser.display_name
            ).outerjoin(CustomerUser, CustomerUser.id == Session.user_id)
            stmt = SessionRepo._apply_filters(
                stmt, user_id, status, last_route_to, date_from, date_to, scene_id
            )
            stmt = stmt.order_by(Session.created_at.desc()).limit(max_rows)
            rows = (await db.execute(stmt)).all()
            result = []
            for s, username, display_name in rows:
                first = (
                    await db.execute(
                        select(Message)
                        .where(Message.session_id == s.id, Message.role == "user")
                        .order_by(Message.created_at.asc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                excerpt = (first.content[:120] if first else "")
                result.append(
                    {
                        "session_id": str(s.id),
                        "customer": display_name or username or ("匿名访客" if s.visitor_id else ""),
                        "username": username or "",
                        "scene_id": str(s.scene_id),
                        "title": s.title,
                        "status": s.status,
                        "last_route_to": s.last_route_to or "",
                        "created_at": s.created_at.isoformat(),
                        "excerpt": excerpt,
                    }
                )
            return result


class MessageRepo:
    @staticmethod
    async def add(
        session_id: uuid.UUID | str,
        role: str,
        content: str,
        routed_to: str | None = None,
        db: AsyncSession | None = None,
        **kw,
    ) -> Message:
        own = db is None
        async with _session(db) as s:
            msg = Message(
                session_id=uuid.UUID(str(session_id)), role=role, content=content, routed_to=routed_to, **kw
            )
            s.add(msg)
            await s.flush()
            if own:
                await s.commit()
                await s.refresh(msg)
            return msg

    @staticmethod
    async def list_recent(
        session_id: uuid.UUID | str,
        limit: int = 50,
        db: AsyncSession | None = None,
    ) -> list[dict]:
        """取最近 N 条消息（时间正序），供聊天上下文使用；不带 trace 关联，避免冗余 JOIN。"""
        async with _session(db) as s:
            rows = (
                (
                    await s.execute(
                        select(Message)
                        .where(Message.session_id == uuid.UUID(str(session_id)))
                        .order_by(Message.created_at.desc())
                        .limit(max(1, limit))
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": str(r.id),
                    "role": r.role,
                    "content": r.content,
                    "routed_to": r.routed_to,
                    "created_at": r.created_at.isoformat(),
                }
                for r in reversed(rows)
            ]

    @staticmethod
    async def count_by_session(session_id: uuid.UUID | str) -> int:
        """会话消息总数（分页 total 必须单独 count，不能用当前页条数代替）。"""
        async with SessionLocal() as db:
            return (
                await db.execute(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.session_id == uuid.UUID(str(session_id)))
                )
            ).scalar_one()

    @staticmethod
    async def list_by_session(
        session_id: uuid.UUID | str, page: int | None = None, page_size: int | None = None
    ) -> list[dict]:
        async with SessionLocal() as db:
            # 顺带带出本轮链路 ID：后台咨询详情才能直接「查看链路」。
            # 链路按 assistant_message_id 一对一定位，走 idx_traces_trace_id 之外的等值过滤即可
            stmt = (
                select(Message, RequestTrace.trace_id)
                .outerjoin(RequestTrace, RequestTrace.assistant_message_id == Message.id)
                .where(Message.session_id == uuid.UUID(str(session_id)))
                .order_by(Message.created_at.asc())
            )
            if page and page_size:
                stmt = stmt.offset((page - 1) * page_size).limit(page_size)
            rows = (await db.execute(stmt)).all()
            return [
                {
                    "id": str(r[0].id),
                    "role": r[0].role,
                    "content": r[0].content,
                    "routed_to": r[0].routed_to,
                    "trace_id": r[1],
                    "created_at": r[0].created_at.isoformat(),
                }
                for r in rows
            ]


class MemoryRepo:
    @staticmethod
    def _actor_cond(subject: uuid.UUID | str) -> list:
        """把隔离主体转成过滤条件：``visitor:<uuid>`` 走 visitor_id，其余走 user_id。"""
        text = str(subject)
        if text.startswith("visitor:"):
            return [Memory.visitor_id == uuid.UUID(text[8:])]
        return [Memory.user_id == uuid.UUID(text)]

    @staticmethod
    async def upsert_mirror(
        user_id: uuid.UUID | str | None = None,
        memory_uid: str = "",
        type_: str = "working",
        content: str = "",
        importance: float = 0.5,
        session_id: uuid.UUID | str | None = None,
        source_message_id: uuid.UUID | str | None = None,
        metadata: dict | None = None,
        db: AsyncSession | None = None,
        visitor_id: uuid.UUID | str | None = None,
    ) -> Memory:
        """原子 upsert：依赖 (user_id, visitor_id, memory_uid) 唯一约束，避免先查后插的并发重复。

        user_id 与 visitor_id 二选一（DB 侧 CHECK 约束）；直接传字符串 ``visitor:<uuid>``
        到 user_id 也会被自动拆解，兼容记忆层沿用的 actor 主体格式。
        """
        own = db is None
        if visitor_id is None and user_id is not None and str(user_id).startswith("visitor:"):
            visitor_id, user_id = str(user_id)[8:], None
        async with _session(db) as s:
            uid = uuid.UUID(str(user_id)) if user_id else None
            vid = uuid.UUID(str(visitor_id)) if visitor_id else None
            src_mid = uuid.UUID(str(source_message_id)) if source_message_id else None
            insert_values = {
                Memory.__table__.c.id: uuid.uuid4(),
                Memory.__table__.c.user_id: uid,
                Memory.__table__.c.visitor_id: vid,
                Memory.__table__.c.memory_uid: memory_uid,
                Memory.__table__.c.type: type_,
                Memory.__table__.c.content: content,
                Memory.__table__.c.importance: importance,
                Memory.__table__.c.session_id: uuid.UUID(str(session_id)) if session_id else None,
                Memory.__table__.c.source_message_id: src_mid,
            }
            # metadata 列名与 Table.metadata 属性同名，用 ORM 属性反查 Column 对象，规避 key 歧义
            insert_values[Memory.metadata_.property.columns[0]] = metadata or {}
            conflict_set = {
                "content": content,
                "importance": importance,
                # 只允许 working → 更高层的单向晋升，避免重复写入把已晋升行降回 working
                "type": case(
                    (Memory.__table__.c.type == "working", type_),
                    else_=Memory.__table__.c.type,
                ),
                "updated_at": func.now(),
            }
            if src_mid is not None:
                # 已有溯源则保留：后续重写（如晋升）传入的 id 不覆盖初次写入的原始消息
                conflict_set["source_message_id"] = func.coalesce(
                    Memory.__table__.c.source_message_id, src_mid
                )
            stmt = (
                pg_insert(Memory)
                .values(insert_values)
                .on_conflict_do_update(
                    index_elements=["user_id", "visitor_id", "memory_uid"],
                    set_=conflict_set,
                )
                .returning(Memory)
            )
            row = (await s.execute(stmt)).scalar_one()
            if own:
                await s.commit()
            return row

    @staticmethod
    async def touch_access(user_id: uuid.UUID | str, prefixes: list[str]) -> list[Memory]:
        """按内容前缀命中镜像记忆行，递增 access_count / 刷新 last_accessed_at。

        user_id 接受 ``user:<uuid>`` / ``visitor:<uuid>`` 两种隔离主体，
        召回结果是截断后的内容前缀，故用等值 OR 前缀匹配；同前缀多行会被连带计数（概率极低）。
        返回本次被更新的行，晋升判定（type/access_count）由调用方完成。
        """
        touched: list[Memory] = []
        seen: set[str] = set()
        actor_cond = MemoryRepo._actor_cond(user_id)
        async with SessionLocal() as db:
            for prefix in prefixes:
                if not prefix or prefix in seen:
                    continue
                seen.add(prefix)
                escaped = (
                    prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                rows = (
                    await db.execute(
                        update(Memory)
                        .where(
                            *actor_cond,
                            or_(Memory.content == prefix, Memory.content.like(escaped + "%")),
                        )
                        .values(
                            access_count=Memory.access_count + 1,
                            last_accessed_at=func.now(),
                        )
                        .returning(Memory)
                    )
                ).scalars().all()
                touched.extend(rows)
            await db.commit()
        return touched

    @staticmethod
    async def promote_working_by_session(
        user_id: uuid.UUID | str, session_id: uuid.UUID | str
    ) -> list[Memory]:
        """会话结束时把该会话未晋升的 working 行全量晋升为 episodic（spec 8.6）。

        user_id 接受 ``user:<uuid>`` / ``visitor:<uuid>`` 两种隔离主体。
        返回被晋升的行，供调用方补写 hello_agents 长期层。
        """
        actor_cond = MemoryRepo._actor_cond(user_id)
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    update(Memory)
                    .where(
                        *actor_cond,
                        Memory.session_id == uuid.UUID(str(session_id)),
                        Memory.type == "working",
                    )
                    .values(type="episodic", updated_at=func.now())
                    .returning(Memory)
                )
            ).scalars().all()
            await db.commit()
        return list(rows)

    @staticmethod
    async def purge_expired_working(ttl_seconds: int) -> int:
        """清理超过 TTL 仍未晋升的 working 镜像行（spec 8.1：working 只有 30 分钟寿命）。

        已晋升行 type 已翻转为 episodic/semantic，不在删除条件内。
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
        async with SessionLocal() as db:
            result = await db.execute(
                delete(Memory).where(Memory.type == "working", Memory.created_at < cutoff)
            )
            await db.commit()
            return result.rowcount or 0

    @staticmethod
    async def list_by_user(user_id: uuid.UUID | str, type_: str | None = None) -> list[Memory]:
        async with SessionLocal() as db:
            stmt = select(Memory).where(Memory.user_id == uuid.UUID(str(user_id)))
            if type_:
                stmt = stmt.where(Memory.type == type_)
            rows = (await db.execute(stmt.order_by(Memory.created_at.desc()))).scalars().all()
            return list(rows)

    @staticmethod
    async def get(memory_id: uuid.UUID | str) -> Memory | None:
        async with SessionLocal() as db:
            return await db.get(Memory, uuid.UUID(str(memory_id)))

    @staticmethod
    async def delete(memory_id: uuid.UUID | str) -> None:
        async with SessionLocal() as db:
            row = await db.get(Memory, uuid.UUID(str(memory_id)))
            if row:
                await db.delete(row)
                await db.commit()


class DocumentRepo:
    @staticmethod
    async def create(title: str, rag_namespace: str, uploaded_by: str | None = None, **kw) -> Document:
        async with SessionLocal() as db:
            doc = Document(
                title=title,
                rag_namespace=rag_namespace,
                uploaded_by=uuid.UUID(str(uploaded_by)) if uploaded_by else None,
                **kw,
            )
            db.add(doc)
            await db.commit()
            await db.refresh(doc)
            return doc

    @staticmethod
    async def update_status(doc_id: uuid.UUID | str, status: str, **kw) -> None:
        async with SessionLocal() as db:
            doc = await db.get(Document, uuid.UUID(str(doc_id)))
            if doc:
                doc.status = status
                for k, v in kw.items():
                    setattr(doc, k, v)
                await db.commit()

    @staticmethod
    async def get(doc_id: uuid.UUID | str) -> Document | None:
        async with SessionLocal() as db:
            return await db.get(Document, uuid.UUID(str(doc_id)))

    @staticmethod
    async def list_all(
        namespace: str | None = None,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        kind: str | None = None,
    ) -> tuple[list[Document], int]:
        async with SessionLocal() as db:
            stmt = select(Document)
            count_stmt = select(func.count()).select_from(Document)
            if namespace:
                stmt = stmt.where(Document.rag_namespace == namespace)
                count_stmt = count_stmt.where(Document.rag_namespace == namespace)
            if status:
                stmt = stmt.where(Document.status == status)
                count_stmt = count_stmt.where(Document.status == status)
            if kind:
                stmt = stmt.where(Document.kind == kind)
                count_stmt = count_stmt.where(Document.kind == kind)
            total = (await db.execute(count_stmt)).scalar_one()
            rows = (
                await db.execute(
                    stmt.order_by(Document.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return list(rows), total

    @staticmethod
    async def count_by_namespace(namespace: str) -> int:
        async with SessionLocal() as db:
            return (
                await db.execute(
                    select(func.count()).select_from(Document).where(Document.rag_namespace == namespace)
                )
            ).scalar_one()

    @staticmethod
    async def delete(doc_id: uuid.UUID | str) -> None:
        async with SessionLocal() as db:
            doc = await db.get(Document, uuid.UUID(str(doc_id)))
            if doc:
                await db.delete(doc)
                await db.commit()


class KnowledgeBaseRepo:
    @staticmethod
    async def list_all() -> list[KnowledgeBase]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at))
            ).scalars().all()
            return list(rows)

    @staticmethod
    async def get(namespace: str) -> KnowledgeBase | None:
        async with SessionLocal() as db:
            return await db.get(KnowledgeBase, namespace)

    @staticmethod
    async def create(
        namespace: str,
        display_name: str,
        description: str | None = None,
        created_by: uuid.UUID | str | None = None,
    ) -> KnowledgeBase:
        """知识库是全局资源：不记场景归属，检索范围由 Agent 的 rag_namespace(s) 决定。

        created_by 只用于登录人配额计数，不参与检索与授权。
        """
        async with SessionLocal() as db:
            row = KnowledgeBase(
                namespace=namespace,
                display_name=display_name,
                description=description,
                created_by=uuid.UUID(str(created_by)) if created_by else None,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def update(namespace: str, **fields) -> KnowledgeBase | None:
        async with SessionLocal() as db:
            row = await db.get(KnowledgeBase, namespace)
            if not row:
                return None
            for k, v in fields.items():
                if v is not None and hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = datetime.now()
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def delete(namespace: str) -> None:
        async with SessionLocal() as db:
            row = await db.get(KnowledgeBase, namespace)
            if row:
                await db.delete(row)
                await db.commit()


class CannedScriptRepo:
    """高频固定话术仓储（热路径每轮读一次，只走 enabled+priority 的窄查询）。

    场景化：``scene_id`` 为空表示全局话术（如 probing 探听拒答），非空表示场景覆盖。
    运行时同一 key 场景行覆盖全局行；后台管理始终能分别看到两行。
    """

    @staticmethod
    def _merge_scene_overrides(rows: list[CannedScript]) -> list[CannedScript]:
        """同 key 场景行覆盖全局行，再按优先级排序（与运行时判定顺序一致）。"""
        by_key: dict[str, CannedScript] = {}
        for row in rows:
            prev = by_key.get(row.key)
            if prev is None or (prev.scene_id is None and row.scene_id is not None):
                by_key[row.key] = row
        return sorted(by_key.values(), key=lambda r: (r.priority, r.key))

    @staticmethod
    async def list_enabled(scene_id: uuid.UUID | str | None = None) -> list[CannedScript]:
        """本场景生效的话术：全局 + 本场景（同 key 场景行覆盖全局，含停用态）。

        先合并再过滤 enabled：场景行被停用时，不会静默回落到同 key 全局行——
        否则运营在场景里关掉一条话术，实际行为却是变成了全局文案。
        """
        async with SessionLocal() as db:
            if scene_id:
                cond = or_(
                    CannedScript.scene_id.is_(None),
                    CannedScript.scene_id == uuid.UUID(str(scene_id)),
                )
            else:
                cond = CannedScript.scene_id.is_(None)
            rows = (
                (await db.execute(select(CannedScript).where(cond))).scalars().all()
            )
            merged = CannedScriptRepo._merge_scene_overrides(list(rows))
            return [r for r in merged if r.enabled]

    @staticmethod
    async def list_all() -> list[CannedScript]:
        """后台全量（全局 + 全部场景），不做覆盖合并：运营需要能看到每个场景的覆盖行。"""
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(CannedScript).order_by(CannedScript.priority, CannedScript.created_at)
                )
            ).scalars().all()
            return list(rows)

    @staticmethod
    async def get_by_key(key: str, scene_id: uuid.UUID | str | None = None) -> CannedScript | None:
        """按 (场景, key) 精确取行：scene_id 为空表示全局行，不回落场景覆盖。"""
        async with SessionLocal() as db:
            stmt = select(CannedScript).where(CannedScript.key == key)
            if scene_id:
                stmt = stmt.where(CannedScript.scene_id == uuid.UUID(str(scene_id)))
            else:
                stmt = stmt.where(CannedScript.scene_id.is_(None))
            return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def create(**fields) -> CannedScript:
        async with SessionLocal() as db:
            row = CannedScript(**fields)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def update(key: str, scene_id: uuid.UUID | str | None = None, **fields) -> CannedScript | None:
        async with SessionLocal() as db:
            stmt = select(CannedScript).where(CannedScript.key == key)
            if scene_id:
                stmt = stmt.where(CannedScript.scene_id == uuid.UUID(str(scene_id)))
            else:
                stmt = stmt.where(CannedScript.scene_id.is_(None))
            row = (await db.execute(stmt)).scalar_one_or_none()
            if not row:
                return None
            for k, v in fields.items():
                if v is not None and hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = datetime.now()
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def set_enabled(
        key: str, enabled: bool, scene_id: uuid.UUID | str | None = None
    ) -> CannedScript | None:
        return await CannedScriptRepo.update(key, scene_id, enabled=enabled)

    @staticmethod
    async def delete(key: str, scene_id: uuid.UUID | str | None = None) -> bool:
        """物理删除（调用方需先挡住 is_builtin，种子话术只允许停用不允许删）。"""
        async with SessionLocal() as db:
            stmt = select(CannedScript).where(CannedScript.key == key)
            if scene_id:
                stmt = stmt.where(CannedScript.scene_id == uuid.UUID(str(scene_id)))
            else:
                stmt = stmt.where(CannedScript.scene_id.is_(None))
            row = (await db.execute(stmt)).scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    @staticmethod
    def to_dict(row: CannedScript) -> dict:
        return {
            "id": str(row.id),
            "scene_id": str(row.scene_id) if row.scene_id else None,
            "key": row.key,
            "title": row.title,
            "category": row.category,
            "patterns": list(row.patterns or []),
            "keywords": list(row.keywords or []),
            "reply_text": row.reply_text or "",
            # 测试替身/历史对象可能没有这两个字段，按默认值兜底
            "answer_source": getattr(row, "answer_source", None) or "text",
            "kb_namespaces": list(getattr(row, "kb_namespaces", None) or []),
            "priority": row.priority,
            "enabled": row.enabled,
            "is_builtin": row.is_builtin,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


class AgentRepo:
    @staticmethod
    async def list_names() -> list[str]:
        async with SessionLocal() as db:
            rows = (await db.execute(select(AgentMeta.name))).scalars().all()
            return list(rows)

    @staticmethod
    async def create(name: str, display_name: str | None = None, description: str | None = None) -> AgentMeta:
        async with SessionLocal() as db:
            row = AgentMeta(name=name, display_name=display_name, description=description, source="code")
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def list_all() -> list[AgentMeta]:
        async with SessionLocal() as db:
            rows = (await db.execute(select(AgentMeta).order_by(AgentMeta.sort_order))).scalars().all()
            return list(rows)

    @staticmethod
    async def list_dynamic(enabled_only: bool = True) -> list[AgentMeta]:
        async with SessionLocal() as db:
            stmt = select(AgentMeta).where(AgentMeta.source == "dynamic")
            if enabled_only:
                stmt = stmt.where(AgentMeta.enabled == True)  # noqa: E712
            rows = (await db.execute(stmt.order_by(AgentMeta.sort_order, AgentMeta.created_at))).scalars().all()
            return list(rows)

    @staticmethod
    async def get_by_name(name: str) -> AgentMeta | None:
        """按名取一条 Agent 元信息；重名时全局 Agent 优先（场景内同名由 M3 路由档案区分）。"""
        async with SessionLocal() as db:
            stmt = (
                select(AgentMeta)
                .where(AgentMeta.name == name)
                .order_by(AgentMeta.scene_id.is_(None).desc(), AgentMeta.created_at)
                .limit(1)
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def set_enabled(name: str, enabled: bool) -> None:
        async with SessionLocal() as db:
            # 同名 Agent 可能分属不同场景：后台按 name 操作时命中的是全局那条
            row = (
                await db.execute(
                    select(AgentMeta)
                    .where(AgentMeta.name == name)
                    .order_by(AgentMeta.scene_id.is_(None).desc(), AgentMeta.created_at)
                    .limit(1)
                )
            ).scalars().first()
            if row:
                row.enabled = enabled
                await db.commit()

    @staticmethod
    async def create_dynamic(**fields) -> AgentMeta:
        async with SessionLocal() as db:
            row = AgentMeta(source="dynamic", **fields)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def update_dynamic(name: str, **fields) -> AgentMeta | None:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(AgentMeta).where(AgentMeta.name == name, AgentMeta.source == "dynamic")
                )
            ).scalar_one_or_none()
            if not row:
                return None
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = datetime.now()
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def delete_dynamic(name: str) -> bool:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(AgentMeta).where(AgentMeta.name == name, AgentMeta.source == "dynamic")
                )
            ).scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    @staticmethod
    async def delete_by_name(name: str) -> bool:
        """按名删除任意 source 的 Agent 行（仅供后台清理“已失效”的孤儿 code 行，调用方需先校验）。"""
        async with SessionLocal() as db:
            row = (await db.execute(select(AgentMeta).where(AgentMeta.name == name))).scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True


class ToolRepo:
    @staticmethod
    async def get_by_name_and_type(name: str, type_: str) -> dict | None:
        async with SessionLocal() as db:
            row = (
                await db.execute(select(ToolMeta).where(ToolMeta.name == name, ToolMeta.type == type_))
            ).scalar_one_or_none()
            if not row:
                return None
            return {"name": row.name, "type": row.type, "schema": row.schema_, "config": row.config}

    @staticmethod
    async def list_all() -> list[ToolMeta]:
        async with SessionLocal() as db:
            rows = (await db.execute(select(ToolMeta))).scalars().all()
            return list(rows)

    @staticmethod
    async def list_by_type(type_: str) -> list[ToolMeta]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(select(ToolMeta).where(ToolMeta.type == type_).order_by(ToolMeta.created_at))
            ).scalars().all()
            return list(rows)

    @staticmethod
    async def get(tool_id: uuid.UUID | str) -> ToolMeta | None:
        async with SessionLocal() as db:
            return await db.get(ToolMeta, uuid.UUID(str(tool_id)))

    @staticmethod
    async def get_by_name(name: str) -> ToolMeta | None:
        async with SessionLocal() as db:
            return (
                await db.execute(
                    select(ToolMeta).where(ToolMeta.name == name, ToolMeta.type == "mcp")
                )
            ).scalar_one_or_none()

    @staticmethod
    async def create_mcp(name: str, config: dict, schema_: dict | None = None) -> ToolMeta:
        async with SessionLocal() as db:
            row = ToolMeta(name=name, type="mcp", schema_=schema_ or {}, config=config)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def create_custom(
        name: str, schema_: dict | None = None, config: dict | None = None, enabled: bool = True
    ) -> ToolMeta:
        """登记一个代码目录里的自定义工具（type=custom）。"""
        async with SessionLocal() as db:
            row = ToolMeta(name=name, type="custom", schema_=schema_ or {}, config=config or {}, enabled=enabled)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def update(tool_id: uuid.UUID | str, **fields) -> ToolMeta | None:
        async with SessionLocal() as db:
            row = await db.get(ToolMeta, uuid.UUID(str(tool_id)))
            if not row:
                return None
            for k, v in fields.items():
                if v is not None and hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = datetime.now()
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def delete(tool_id: uuid.UUID | str) -> bool:
        async with SessionLocal() as db:
            row = await db.get(ToolMeta, uuid.UUID(str(tool_id)))
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True


class AuditRepo:
    @staticmethod
    async def write(
        action: str,
        user_id: uuid.UUID | str | None = None,
        session_id: uuid.UUID | str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        payload: dict | None = None,
        ip: str | None = None,
    ) -> None:
        async with SessionLocal() as db:
            db.add(
                AuditLog(
                    user_id=uuid.UUID(str(user_id)) if user_id else None,
                    session_id=uuid.UUID(str(session_id)) if session_id else None,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    payload=payload,
                    ip=ip,
                )
            )
            await db.commit()


def _ticket_uuid(value) -> uuid.UUID | None:
    """尽力转 UUID；示例客户名（alice）等非 UUID 返回 None，不阻断工单写入。"""
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _ticket_now() -> str:
    return datetime.now(UTC).isoformat()


class TicketRepo:
    """客诉工单仓储：结构化增改查（取代此前散落在 audit_logs.payload 的工单记录）。"""

    STATUSES = ("open", "in_progress", "escalated", "resolved", "closed")

    @staticmethod
    def new_ticket_no() -> str:
        return f"TK-{uuid.uuid4().hex[:8].upper()}"

    @staticmethod
    def to_dict(t: Ticket, *, include_updates: bool = False) -> dict:
        data = {
            "id": str(t.id),
            "ticket_no": t.ticket_no,
            "user_id": str(t.user_id) if t.user_id else None,
            "session_id": str(t.session_id) if t.session_id else None,
            "category": t.category,
            "urgency": t.urgency,
            "status": t.status,
            "title": t.title,
            "content": t.content,
            "metadata": dict(t.metadata_ or {}),
            "update_count": len(t.updates or []),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        if include_updates:
            data["updates"] = list(t.updates or [])
        return data

    @staticmethod
    async def create(
        content: str,
        *,
        ticket_no: str | None = None,
        user_id=None,
        session_id=None,
        category: str = "general",
        urgency: str = "medium",
        title: str | None = None,
        metadata_: dict | None = None,
    ) -> Ticket:
        async with SessionLocal() as db:
            row = Ticket(
                ticket_no=ticket_no or TicketRepo.new_ticket_no(),
                user_id=_ticket_uuid(user_id),
                session_id=_ticket_uuid(session_id),
                category=category or "general",
                urgency=urgency if urgency in ("low", "medium", "high") else "medium",
                status="open",
                title=(title or content)[:256],
                content=content[:4000],
                updates=[],
                metadata_=metadata_ or {},
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def get_by_ticket_no(ticket_no: str) -> Ticket | None:
        async with SessionLocal() as db:
            return (
                await db.execute(select(Ticket).where(Ticket.ticket_no == str(ticket_no)))
            ).scalar_one_or_none()

    @staticmethod
    async def get(ticket_id: uuid.UUID | str) -> Ticket | None:
        async with SessionLocal() as db:
            return await db.get(Ticket, uuid.UUID(str(ticket_id)))

    @staticmethod
    async def append_update(ticket_no: str, content: str) -> Ticket | None:
        """补录一条更新：追加到 updates 历史并把 content 刷为最新概述。找不到工单返回 None。"""
        async with SessionLocal() as db:
            row = (
                await db.execute(select(Ticket).where(Ticket.ticket_no == str(ticket_no)))
            ).scalar_one_or_none()
            if row is None:
                return None
            row.updates = list(row.updates or []) + [{"content": content[:2000], "at": _ticket_now()}]
            row.content = content[:4000]
            if row.status == "open":
                row.status = "in_progress"
            row.updated_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def set_status(ticket_no: str, status: str, note: str | None = None) -> Ticket | None:
        async with SessionLocal() as db:
            row = (
                await db.execute(select(Ticket).where(Ticket.ticket_no == str(ticket_no)))
            ).scalar_one_or_none()
            if row is None:
                return None
            if status not in TicketRepo.STATUSES:
                raise ValueError(f"非法工单状态: {status}")
            row.status = status
            if note:
                row.updates = list(row.updates or []) + [
                    {"content": note[:2000], "at": _ticket_now(), "action": f"status->{status}"}
                ]
            row.updated_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def latest_open_for(user_id=None, session_id=None) -> Ticket | None:
        """取该客户/会话最近一张未关闭工单（供 escalate 关联）。"""
        uid = _ticket_uuid(user_id)
        sid = _ticket_uuid(session_id)
        if uid is None and sid is None:
            return None
        async with SessionLocal() as db:
            stmt = select(Ticket).where(Ticket.status.in_(("open", "in_progress")))
            if uid is not None and sid is not None:
                stmt = stmt.where((Ticket.user_id == uid) | (Ticket.session_id == sid))
            elif uid is not None:
                stmt = stmt.where(Ticket.user_id == uid)
            else:
                stmt = stmt.where(Ticket.session_id == sid)
            return (
                await db.execute(stmt.order_by(Ticket.created_at.desc()).limit(1))
            ).scalar_one_or_none()

    @staticmethod
    def _apply_filters(stmt, *, user_id, status, category, urgency, keyword, date_from, date_to):
        uid = _ticket_uuid(user_id)
        if uid is not None:
            stmt = stmt.where(Ticket.user_id == uid)
        if status:
            stmt = stmt.where(Ticket.status == status)
        if category:
            stmt = stmt.where(Ticket.category == category)
        if urgency:
            stmt = stmt.where(Ticket.urgency == urgency)
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(Ticket.content.ilike(like) | Ticket.ticket_no.ilike(like))
        if date_from:
            stmt = stmt.where(Ticket.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Ticket.created_at <= date_to)
        return stmt

    @staticmethod
    async def list_filtered(
        *,
        user_id=None,
        status: str | None = None,
        category: str | None = None,
        urgency: str | None = None,
        keyword: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        kw = {
            "user_id": user_id,
            "status": status,
            "category": category,
            "urgency": urgency,
            "keyword": keyword,
            "date_from": date_from,
            "date_to": date_to,
        }
        async with SessionLocal() as db:
            base = TicketRepo._apply_filters(select(Ticket), **kw)
            count_base = TicketRepo._apply_filters(select(func.count()).select_from(Ticket), **kw)
            total = (await db.execute(count_base)).scalar_one()
            rows = (
                (
                    await db.execute(
                        base.order_by(Ticket.created_at.desc())
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .scalars()
                .all()
            )
            return [TicketRepo.to_dict(t) for t in rows], total


def _trace_uuid(value) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


class TraceRepo:
    """请求链路仓储：写入 + 后台多维过滤查询。"""

    @staticmethod
    async def insert(row: dict) -> None:
        """写入一轮链路。调用方负责降级，链路失败不能影响对话。"""
        async with SessionLocal() as db:
            db.add(
                RequestTrace(
                    id=_trace_uuid(row.get("id")) or uuid.uuid4(),
                    trace_id=str(row.get("trace_id") or "")[:64],
                    session_id=_trace_uuid(row.get("session_id")),
                    user_id=_trace_uuid(row.get("user_id")),
                    scene_code=str(row.get("scene_code") or "default")[:32],
                    visitor_id=_trace_uuid(row.get("visitor_id")),
                    user_message_id=_trace_uuid(row.get("user_message_id")),
                    assistant_message_id=_trace_uuid(row.get("assistant_message_id")),
                    intent_count=row.get("intent_count") or 0,
                    router_skipped=bool(row.get("router_skipped")),
                    router_fallback=bool(row.get("router_fallback")),
                    router_ms=row.get("router_ms"),
                    prompt_hash=(str(row["prompt_hash"])[:64] if row.get("prompt_hash") else None),
                    agent_names=row.get("agent_names") or [],
                    agent_sources=row.get("agent_sources") or [],
                    llm_models=row.get("llm_models") or [],
                    mcp_server_names=row.get("mcp_server_names") or [],
                    tool_names=row.get("tool_names") or [],
                    rag_namespaces=row.get("rag_namespaces") or [],
                    memory_hit_count=row.get("memory_hit_count") or 0,
                    rag_chunk_count=row.get("rag_chunk_count") or 0,
                    tool_call_count=row.get("tool_call_count") or 0,
                    tool_error_count=row.get("tool_error_count") or 0,
                    confidence=row.get("confidence"),
                    outcome=row.get("outcome"),
                    handover_reason=row.get("handover_reason"),
                    error_code=row.get("error_code"),
                    error_message=row.get("error_message"),
                    ttft_ms=row.get("ttft_ms"),
                    total_latency_ms=row.get("total_latency_ms"),
                    response_chars=row.get("response_chars") or 0,
                    spans=row.get("spans") or [],
                )
            )
            await db.commit()

    @staticmethod
    def _summary(t: RequestTrace) -> dict:
        """列表页用的摘要（不带 spans，避免一页拉几 MB JSONB）。"""
        return {
            "id": str(t.id),
            "trace_id": t.trace_id,
            "session_id": str(t.session_id) if t.session_id else None,
            "user_id": str(t.user_id) if t.user_id else None,
            "scene_code": t.scene_code,
            "router_skipped": t.router_skipped,
            "router_fallback": t.router_fallback,
            "router_ms": t.router_ms,
            "prompt_hash": t.prompt_hash,
            "user_message_id": str(t.user_message_id) if t.user_message_id else None,
            "assistant_message_id": str(t.assistant_message_id) if t.assistant_message_id else None,
            "intent_count": t.intent_count,
            "agent_names": list(t.agent_names or []),
            "agent_sources": list(t.agent_sources or []),
            "llm_models": list(t.llm_models or []),
            "mcp_server_names": list(t.mcp_server_names or []),
            "tool_names": list(t.tool_names or []),
            "rag_namespaces": list(t.rag_namespaces or []),
            "memory_hit_count": t.memory_hit_count,
            "rag_chunk_count": t.rag_chunk_count,
            "tool_call_count": t.tool_call_count,
            "tool_error_count": t.tool_error_count,
            "confidence": t.confidence,
            "outcome": t.outcome,
            "handover_reason": t.handover_reason,
            "error_code": t.error_code,
            "error_message": t.error_message,
            "ttft_ms": t.ttft_ms,
            "total_latency_ms": t.total_latency_ms,
            "response_chars": t.response_chars,
            "span_count": len(t.spans or []),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    @staticmethod
    def _apply_filters(
        stmt,
        *,
        session_id,
        user_id,
        scene_code,
        agent_name,
        tool_name,
        mcp_server,
        outcome,
        has_error,
        min_latency_ms,
        date_from,
        date_to,
    ):
        if session_id:
            stmt = stmt.where(RequestTrace.session_id == uuid.UUID(str(session_id)))
        if user_id:
            stmt = stmt.where(RequestTrace.user_id == uuid.UUID(str(user_id)))
        if scene_code:
            stmt = stmt.where(RequestTrace.scene_code == str(scene_code))
        # 数组列用 @> 包含判定（GIN 索引可命中）
        if agent_name:
            stmt = stmt.where(RequestTrace.agent_names.contains([agent_name]))
        if tool_name:
            stmt = stmt.where(RequestTrace.tool_names.contains([tool_name]))
        if mcp_server:
            stmt = stmt.where(RequestTrace.mcp_server_names.contains([mcp_server]))
        if outcome:
            stmt = stmt.where(RequestTrace.outcome == outcome)
        if has_error:
            stmt = stmt.where(
                (RequestTrace.outcome == "error") | (RequestTrace.tool_error_count > 0)
            )
        if min_latency_ms:
            stmt = stmt.where(RequestTrace.total_latency_ms >= int(min_latency_ms))
        if date_from:
            stmt = stmt.where(RequestTrace.created_at >= date_from)
        if date_to:
            stmt = stmt.where(RequestTrace.created_at <= date_to)
        return stmt

    @staticmethod
    async def list_filtered(
        *,
        session_id=None,
        user_id=None,
        scene_code=None,
        agent_name=None,
        tool_name=None,
        mcp_server=None,
        outcome=None,
        has_error: bool = False,
        min_latency_ms=None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        kw = {
            "session_id": session_id,
            "user_id": user_id,
            "scene_code": scene_code,
            "agent_name": agent_name,
            "tool_name": tool_name,
            "mcp_server": mcp_server,
            "outcome": outcome,
            "has_error": has_error,
            "min_latency_ms": min_latency_ms,
            "date_from": date_from,
            "date_to": date_to,
        }
        async with SessionLocal() as db:
            base = TraceRepo._apply_filters(select(RequestTrace), **kw)
            count_base = TraceRepo._apply_filters(
                select(func.count()).select_from(RequestTrace), **kw
            )
            total = (await db.execute(count_base)).scalar_one()
            rows = (
                (
                    await db.execute(
                        base.order_by(RequestTrace.created_at.desc())
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .scalars()
                .all()
            )
            return [TraceRepo._summary(t) for t in rows], total

    @staticmethod
    async def get(trace_ref: str) -> RequestTrace | None:
        """按行 ID / HTTP trace_id / 助手消息 ID 依次尝试取一条完整链路。"""
        async with SessionLocal() as db:
            try:
                row = await db.get(RequestTrace, uuid.UUID(str(trace_ref)))
            except (ValueError, TypeError):
                row = None
            if row is not None:
                return row
            row = (
                await db.execute(
                    select(RequestTrace)
                    .where(RequestTrace.trace_id == str(trace_ref))
                    .order_by(RequestTrace.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is not None:
                return row
            try:
                msg_id = uuid.UUID(str(trace_ref))
            except (ValueError, TypeError):
                return None
            return (
                await db.execute(
                    select(RequestTrace).where(RequestTrace.assistant_message_id == msg_id).limit(1)
                )
            ).scalar_one_or_none()

    @staticmethod
    async def detail(trace_ref: str) -> dict | None:
        row = await TraceRepo.get(trace_ref)
        if row is None:
            return None
        data = TraceRepo._summary(row)
        data["spans"] = list(row.spans or [])
        return data

    @staticmethod
    async def list_by_session(session_id) -> list[dict]:
        """某会话按轮次的链路（时间正序），供详情页画会话级时间线。"""
        async with SessionLocal() as db:
            rows = (
                (
                    await db.execute(
                        select(RequestTrace)
                        .where(RequestTrace.session_id == uuid.UUID(str(session_id)))
                        .order_by(RequestTrace.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            return [TraceRepo._summary(t) for t in rows]

    @staticmethod
    async def usage_counts(field: str = "tool_names", days: int = 7, max_rows: int = 5000) -> list[dict]:
        """近 N 天内各工具 / Agent / MCP 服务被多少轮请求覆盖（Python 侧聚合，
        不依赖 PG unnest 写法，便于单测与不同版本兼容）。"""
        column = getattr(RequestTrace, field, None)
        if column is None:
            return []
        since = datetime.now(UTC) - timedelta(days=days)
        async with SessionLocal() as db:
            values = (
                (
                    await db.execute(
                        select(column).where(RequestTrace.created_at >= since).limit(max_rows)
                    )
                )
                .scalars()
                .all()
            )
        counter: dict[str, int] = {}
        for names in values:
            for name in names or []:
                counter[name] = counter.get(name, 0) + 1
        return [{"name": k, "count": c} for k, c in sorted(counter.items(), key=lambda x: -x[1])]

    @staticmethod
    async def router_stats(scene_code: str | None = None, hours: int = 24) -> dict:
        """近 N 小时的路由质量聚合（可按场景过滤）。

        只统计已完成并且写入了链路的请求：客户端中途断连的轮次不写入，
        因此分母是“真正走完的请求”，比率不会被半截请求拉低。
        """
        window = max(1, int(hours))
        since = datetime.now(UTC) - timedelta(hours=window)
        stmt = select(
            func.count().label("requests"),
            func.count().filter(RequestTrace.router_skipped.is_(True)).label("router_skipped"),
            func.count().filter(RequestTrace.router_fallback.is_(True)).label("router_fallback"),
            func.count().filter(RequestTrace.outcome == "handover").label("handover"),
            func.avg(RequestTrace.router_ms).label("avg_router_ms"),
            func.avg(RequestTrace.total_latency_ms).label("avg_total_ms"),
        ).where(RequestTrace.created_at >= since)
        if scene_code:
            stmt = stmt.where(RequestTrace.scene_code == scene_code)
        async with SessionLocal() as db:
            row = (await db.execute(stmt)).one()

        total = int(row.requests or 0)

        def _rate(count) -> float:
            return round(int(count or 0) / total, 4) if total else 0.0

        return {
            "scene_code": scene_code,
            "hours": window,
            "requests": total,
            "router_skipped": int(row.router_skipped or 0),
            "router_fallback": int(row.router_fallback or 0),
            "handover": int(row.handover or 0),
            "skip_rate": _rate(row.router_skipped),
            "fallback_rate": _rate(row.router_fallback),
            "handover_rate": _rate(row.handover),
            "avg_router_ms": (
                round(float(row.avg_router_ms), 1) if row.avg_router_ms is not None else None
            ),
            "avg_total_latency_ms": (
                round(float(row.avg_total_ms), 1) if row.avg_total_ms is not None else None
            ),
        }

    @staticmethod
    async def delete_older_than(days: int) -> int:
        """保留期清理（由 scripts/ 定期调用）。"""
        cutoff = datetime.now(UTC) - timedelta(days=max(1, days))
        async with SessionLocal() as db:
            result = await db.execute(
                RequestTrace.__table__.delete().where(RequestTrace.created_at < cutoff)
            )
            await db.commit()
            return int(result.rowcount or 0)


class ChatEventRepo:
    """对话埋点：旁路写入，全量记录，不受 request_traces 采样影响。"""

    @staticmethod
    def _safe_uuid(value) -> uuid.UUID | None:
        """客户端传入的 session/visitor 可能不是 UUID：落库前统一兜底为 None。"""
        try:
            return uuid.UUID(str(value)) if value else None
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    async def record(
        *,
        trace_id: str,
        scene_code: str,
        session_id=None,
        visitor_id=None,
        user_id=None,
        integration_id=None,
        ext: dict | None = None,
        message_chars: int = 0,
    ) -> None:
        async with SessionLocal() as db:
            db.add(
                ChatEvent(
                    trace_id=str(trace_id or "")[:64],
                    scene_code=str(scene_code or "default")[:32],
                    session_id=ChatEventRepo._safe_uuid(session_id),
                    visitor_id=ChatEventRepo._safe_uuid(visitor_id),
                    user_id=ChatEventRepo._safe_uuid(user_id),
                    integration_id=ChatEventRepo._safe_uuid(integration_id),
                    ext=ext or {},
                    message_chars=max(int(message_chars or 0), 0),
                )
            )
            await db.commit()

    @staticmethod
    async def count_by_scene_today() -> dict[str, int]:
        """今日各场景请求数（一次 GROUP BY，供后台场景列表展示用量）。

        “今日”按进程本地时区 00:00 起算，与 Redis 日额度桶（time.strftime）同口径；
        被限流拦住的请求也已在埋点里（埋点在限流之前），所以用量含被拒的请求。
        """
        since = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(ChatEvent.scene_code, func.count())
                    .where(ChatEvent.created_at >= since)
                    .group_by(ChatEvent.scene_code)
                )
            ).all()
        return {str(code): int(count) for code, count in rows}

    @staticmethod
    async def attach_identity(
        *,
        trace_id: str,
        session_id=None,
        visitor_id=None,
        user_id=None,
    ) -> int:
        """按 trace_id 回填服务端解析出的真实身份，返回更新行数。

        埋点在会话/访客创建之前写入：首轮 session_id 为空，匿名请求带的是
        external_key（v_xxx，不是 UUID）。这里用 sessions.id / visitors.id
        覆盖，保证 chat_events 能 join 会话与访客；无可用字段时直接跳过。
        """
        tid = str(trace_id or "")[:64]
        values = {}
        for key, raw in (
            ("session_id", session_id),
            ("visitor_id", visitor_id),
            ("user_id", user_id),
        ):
            parsed = ChatEventRepo._safe_uuid(raw)
            if parsed is not None:
                values[key] = parsed
        if not tid or not values:
            return 0
        async with SessionLocal() as db:
            result = await db.execute(
                update(ChatEvent).where(ChatEvent.trace_id == tid).values(**values)
            )
            await db.commit()
            return int(result.rowcount or 0)
