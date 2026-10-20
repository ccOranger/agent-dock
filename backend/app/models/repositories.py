"""仓储层：对 ORM 的异步封装（Repo 模式）"""
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.entities import (
    AgentMeta,
    AuditLog,
    CannedScript,
    CustomerUser,
    Document,
    KnowledgeBase,
    Memory,
    Message,
    RequestTrace,
    Session,
    StaffUser,
    Ticket,
    ToolMeta,
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
    async def create(user_id: uuid.UUID | str, title: str = "新对话") -> Session:
        async with SessionLocal() as db:
            sess = Session(user_id=uuid.UUID(str(user_id)), title=title)
            db.add(sess)
            await db.commit()
            await db.refresh(sess)
            return sess

    @staticmethod
    async def get(session_id: uuid.UUID | str, db: AsyncSession | None = None) -> Session | None:
        async with _session(db) as s:
            return await s.get(Session, uuid.UUID(str(session_id)))

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
            base = select(Session, CustomerUser.username, CustomerUser.display_name).join(
                CustomerUser, CustomerUser.id == Session.user_id
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
                Session, CustomerUser.username, CustomerUser.display_name
            ).join(CustomerUser, CustomerUser.id == Session.user_id)
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
    async def upsert_mirror(
        user_id: uuid.UUID | str,
        memory_uid: str,
        type_: str,
        content: str,
        importance: float = 0.5,
        session_id: uuid.UUID | str | None = None,
        source_message_id: uuid.UUID | str | None = None,
        metadata: dict | None = None,
        db: AsyncSession | None = None,
    ) -> Memory:
        """原子 upsert：依赖 (user_id, memory_uid) 唯一约束，避免先查后插的并发重复。"""
        own = db is None
        async with _session(db) as s:
            uid = uuid.UUID(str(user_id))
            src_mid = uuid.UUID(str(source_message_id)) if source_message_id else None
            insert_values = {
                Memory.__table__.c.id: uuid.uuid4(),
                Memory.__table__.c.user_id: uid,
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
                    index_elements=["user_id", "memory_uid"],
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

        召回结果是截断后的内容前缀，故用等值 OR 前缀匹配；同前缀多行会被连带计数（概率极低）。
        返回本次被更新的行，晋升判定（type/access_count）由调用方完成。
        """
        touched: list[Memory] = []
        seen: set[str] = set()
        async with SessionLocal() as db:
            uid = uuid.UUID(str(user_id))
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
                            Memory.user_id == uid,
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

        返回被晋升的行，供调用方补写 hello_agents 长期层。
        """
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    update(Memory)
                    .where(
                        Memory.user_id == uuid.UUID(str(user_id)),
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


class CannedScriptRepo:
    """高频固定话术仓储（热路径每轮读一次，只走 enabled+priority 的窄查询）。"""

    @staticmethod
    async def list_enabled() -> list[CannedScript]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(CannedScript)
                    .where(CannedScript.enabled == True)  # noqa: E712
                    .order_by(CannedScript.priority, CannedScript.created_at)
                )
            ).scalars().all()
            return list(rows)

    @staticmethod
    async def list_all() -> list[CannedScript]:
        async with SessionLocal() as db:
            rows = (
                await db.execute(select(CannedScript).order_by(CannedScript.priority, CannedScript.created_at))
            ).scalars().all()
            return list(rows)

    @staticmethod
    async def get_by_key(key: str) -> CannedScript | None:
        async with SessionLocal() as db:
            return (
                await db.execute(select(CannedScript).where(CannedScript.key == key))
            ).scalar_one_or_none()

    @staticmethod
    async def create(**fields) -> CannedScript:
        async with SessionLocal() as db:
            row = CannedScript(**fields)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def update(key: str, **fields) -> CannedScript | None:
        async with SessionLocal() as db:
            row = (
                await db.execute(select(CannedScript).where(CannedScript.key == key))
            ).scalar_one_or_none()
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
    async def set_enabled(key: str, enabled: bool) -> CannedScript | None:
        async with SessionLocal() as db:
            row = (
                await db.execute(select(CannedScript).where(CannedScript.key == key))
            ).scalar_one_or_none()
            if not row:
                return None
            row.enabled = enabled
            row.updated_at = datetime.now()
            await db.commit()
            await db.refresh(row)
            return row

    @staticmethod
    async def delete(key: str) -> bool:
        """物理删除（调用方需先挡住 is_builtin，种子话术只允许停用不允许删）。"""
        async with SessionLocal() as db:
            row = (
                await db.execute(select(CannedScript).where(CannedScript.key == key))
            ).scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    @staticmethod
    def to_dict(row: CannedScript) -> dict:
        return {
            "id": str(row.id),
            "key": row.key,
            "title": row.title,
            "category": row.category,
            "patterns": list(row.patterns or []),
            "keywords": list(row.keywords or []),
            "reply_text": row.reply_text or "",
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
    async def set_enabled_many(names: list[str], enabled: bool) -> int:
        """批量设置已存在的 Agent 行（供场景包整体启停），返回受影响行数。"""
        if not names:
            return 0
        async with SessionLocal() as db:
            rows = (
                (await db.execute(select(AgentMeta).where(AgentMeta.name.in_(names)))).scalars().all()
            )
            for row in rows:
                row.enabled = enabled
            await db.commit()
            return len(rows)

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
                    user_message_id=_trace_uuid(row.get("user_message_id")),
                    assistant_message_id=_trace_uuid(row.get("assistant_message_id")),
                    intent_count=row.get("intent_count") or 0,
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
    async def delete_older_than(days: int) -> int:
        """保留期清理（由 scripts/ 定期调用）。"""
        cutoff = datetime.now(UTC) - timedelta(days=max(1, days))
        async with SessionLocal() as db:
            result = await db.execute(
                RequestTrace.__table__.delete().where(RequestTrace.created_at < cutoff)
            )
            await db.commit()
            return int(result.rowcount or 0)
