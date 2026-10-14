"""仓储层：对 ORM 的异步封装（Repo 模式）"""
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.entities import (
    AgentMeta,
    AuditLog,
    Document,
    KnowledgeBase,
    Memory,
    Message,
    Session,
    ToolMeta,
    User,
)


class UserRepo:
    @staticmethod
    async def get_by_username(username: str) -> User | None:
        async with SessionLocal() as db:
            return (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()

    @staticmethod
    async def get_by_id(user_id: uuid.UUID | str) -> User | None:
        async with SessionLocal() as db:
            return await db.get(User, uuid.UUID(str(user_id)))

    @staticmethod
    async def create(username: str, password_hash: str, role: str = "customer", **kw) -> User:
        async with SessionLocal() as db:
            user = User(username=username, password_hash=password_hash, role=role, **kw)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user


class SessionRepo:
    @staticmethod
    async def get_or_create(user_id: uuid.UUID | str, session_id: uuid.UUID | str | None) -> Session:
        async with SessionLocal() as db:
            if session_id:
                sess = await db.get(Session, uuid.UUID(str(session_id)))
                if sess and str(sess.user_id) == str(user_id):
                    return sess
            sess = Session(user_id=uuid.UUID(str(user_id)))
            db.add(sess)
            await db.commit()
            await db.refresh(sess)
            return sess

    @staticmethod
    async def create(user_id: uuid.UUID | str, title: str = "新对话") -> Session:
        async with SessionLocal() as db:
            sess = Session(user_id=uuid.UUID(str(user_id)), title=title)
            db.add(sess)
            await db.commit()
            await db.refresh(sess)
            return sess

    @staticmethod
    async def get(session_id: uuid.UUID | str) -> Session | None:
        async with SessionLocal() as db:
            return await db.get(Session, uuid.UUID(str(session_id)))

    @staticmethod
    async def list_by_user(user_id: uuid.UUID | str, page: int = 1, page_size: int = 20) -> tuple[list[Session], int]:
        async with SessionLocal() as db:
            total = (
                await db.execute(
                    select(func.count()).select_from(Session).where(Session.user_id == uuid.UUID(str(user_id)))
                )
            ).scalar_one()
            rows = (
                await db.execute(
                    select(Session)
                    .where(Session.user_id == uuid.UUID(str(user_id)))
                    .order_by(Session.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return list(rows), total

    @staticmethod
    async def set_last_route_to(session_id: uuid.UUID | str, agent_name: str) -> None:
        async with SessionLocal() as db:
            sess = await db.get(Session, uuid.UUID(str(session_id)))
            if sess:
                sess.last_route_to = agent_name
                await db.commit()

    @staticmethod
    def _apply_filters(stmt, user_id, status, last_route_to, date_from, date_to):
        if user_id:
            stmt = stmt.where(Session.user_id == uuid.UUID(str(user_id)))
        if status:
            stmt = stmt.where(Session.status == status)
        if last_route_to:
            stmt = stmt.where(Session.last_route_to == last_route_to)
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
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """跨客户会话查询（管理后台）：JOIN 用户带出客户名 + 消息数。"""
        msg_count = (
            select(func.count())
            .select_from(Message)
            .where(Message.session_id == Session.id)
            .correlate(Session)
            .scalar_subquery()
            .label("message_count")
        )
        async with SessionLocal() as db:
            base = select(Session, User.username, User.display_name).join(
                User, User.id == Session.user_id
            )
            count_base = select(func.count()).select_from(Session)
            base = SessionRepo._apply_filters(
                base, user_id, status, last_route_to, date_from, date_to
            )
            count_base = SessionRepo._apply_filters(
                count_base, user_id, status, last_route_to, date_from, date_to
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
                    "user_id": str(s.user_id),
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
        max_rows: int = 10000,
    ) -> list[dict]:
        """导出用：返回满足过滤条件的会话（含首条客户问句摘要）。"""
        async with SessionLocal() as db:
            stmt = select(
                Session, User.username, User.display_name
            ).join(User, User.id == Session.user_id)
            stmt = SessionRepo._apply_filters(
                stmt, user_id, status, last_route_to, date_from, date_to
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
                        "customer": display_name or username,
                        "username": username,
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
        **kw,
    ) -> Message:
        async with SessionLocal() as db:
            msg = Message(session_id=uuid.UUID(str(session_id)), role=role, content=content, routed_to=routed_to, **kw)
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg

    @staticmethod
    async def list_by_session(
        session_id: uuid.UUID | str, page: int | None = None, page_size: int | None = None
    ) -> list[dict]:
        async with SessionLocal() as db:
            stmt = (
                select(Message).where(Message.session_id == uuid.UUID(str(session_id))).order_by(Message.created_at.asc())
            )
            if page and page_size:
                stmt = stmt.offset((page - 1) * page_size).limit(page_size)
            rows = (await db.execute(stmt)).scalars().all()
            return [
                {
                    "id": str(r.id),
                    "role": r.role,
                    "content": r.content,
                    "routed_to": r.routed_to,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]


class MemoryRepo:
    @staticmethod
    async def upsert_mirror(
        user_id: uuid.UUID | str,
        memory_uid: str,
        type_: str,
        content: str,
        importance: float = 0.5,
        session_id: uuid.UUID | str | None = None,
        metadata: dict | None = None,
    ) -> Memory:
        async with SessionLocal() as db:
            uid = uuid.UUID(str(user_id))
            existing = (
                await db.execute(
                    select(Memory).where(Memory.user_id == uid, Memory.memory_uid == memory_uid)
                )
            ).scalar_one_or_none()
            if existing:
                existing.content = content
                existing.importance = importance
                existing.updated_at = datetime.now()
                await db.commit()
                await db.refresh(existing)
                return existing
            row = Memory(
                user_id=uid,
                memory_uid=memory_uid,
                type=type_,
                content=content,
                importance=importance,
                session_id=uuid.UUID(str(session_id)) if session_id else None,
                metadata_=metadata or {},
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

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
                await db.execute(stmt.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
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
    async def create(namespace: str, display_name: str, description: str | None = None) -> KnowledgeBase:
        async with SessionLocal() as db:
            row = KnowledgeBase(
                namespace=namespace, display_name=display_name, description=description
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
        async with SessionLocal() as db:
            return (await db.execute(select(AgentMeta).where(AgentMeta.name == name))).scalar_one_or_none()

    @staticmethod
    async def set_enabled(name: str, enabled: bool) -> None:
        async with SessionLocal() as db:
            row = (await db.execute(select(AgentMeta).where(AgentMeta.name == name))).scalar_one_or_none()
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
