"""仓储层：对 ORM 的异步封装（Repo 模式）"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.entities import (
    AgentMeta,
    AuditLog,
    CustomerUser,
    Document,
    KnowledgeBase,
    Memory,
    Message,
    RequestTrace,
    Session,
    StaffUser,
    ToolMeta,
)


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
        since = datetime.now(timezone.utc) - timedelta(days=days)
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
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        async with SessionLocal() as db:
            result = await db.execute(
                RequestTrace.__table__.delete().where(RequestTrace.created_at < cutoff)
            )
            await db.commit()
            return int(result.rowcount or 0)
