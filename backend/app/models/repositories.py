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
    async def list_all(namespace: str | None = None, page: int = 1, page_size: int = 20) -> tuple[list[Document], int]:
        async with SessionLocal() as db:
            stmt = select(Document)
            count_stmt = select(func.count()).select_from(Document)
            if namespace:
                stmt = stmt.where(Document.rag_namespace == namespace)
                count_stmt = count_stmt.where(Document.rag_namespace == namespace)
            total = (await db.execute(count_stmt)).scalar_one()
            rows = (
                await db.execute(stmt.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
            ).scalars().all()
            return list(rows), total

    @staticmethod
    async def delete(doc_id: uuid.UUID | str) -> None:
        async with SessionLocal() as db:
            doc = await db.get(Document, uuid.UUID(str(doc_id)))
            if doc:
                await db.delete(doc)
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
            row = AgentMeta(name=name, display_name=display_name, description=description)
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
