"""知识库管理测试：命名空间 CRUD、手工条目入库、分块查看（Repo/Qdrant/RAGTool 均 mock，不依赖真实 DB）"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as kn
from app.services import qdrant_helper as qh

DOC_ID = str(uuid.uuid4())


def _doc(**kw) -> SimpleNamespace:
    base = {
        "id": DOC_ID,
        "title": "退货说明",
        "rag_namespace": "cs_after_sales",
        "kind": "manual",
        "status": "ready",
        "chunk_count": 2,
        "content_type": "text/markdown",
        "error_message": None,
        "source": qh.manual_source_path(DOC_ID),
        "created_at": __import__("datetime").datetime(2025, 1, 1),
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def staff_client(make_test_app):
    app = make_test_app([kn.router], prefix="")
    return TestClient(make_test_app.as_role(app, "agent"))


@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([kn.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


# ===== 命名空间 CRUD =====
def test_list_bases_with_document_count(staff_client, monkeypatch):
    bases = [
        SimpleNamespace(
            namespace="cs_order", display_name="订单", description=None, is_active=True
        )
    ]

    async def fake_list_all():
        return bases

    async def fake_count(namespace):
        return 7

    monkeypatch.setattr(kn.KnowledgeBaseRepo, "list_all", fake_list_all)
    monkeypatch.setattr(kn.DocumentRepo, "count_by_namespace", fake_count)

    resp = staff_client.get(f"{kn.router.prefix}/bases")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["namespace"] == "cs_order"
    assert data[0]["document_count"] == 7


def test_create_base_success_and_conflict(admin_client, monkeypatch):
    existing: set[str] = set()

    async def fake_get(namespace):
        return SimpleNamespace(namespace=namespace) if namespace in existing else None

    async def fake_create(namespace, display_name, description, created_by=None):
        existing.add(namespace)
        return SimpleNamespace(
            namespace=namespace, display_name=display_name, is_active=True
        )

    monkeypatch.setattr(kn.KnowledgeBaseRepo, "get", fake_get)
    monkeypatch.setattr(kn.KnowledgeBaseRepo, "create", fake_create)

    body = {"namespace": "cs_vip", "display_name": "会员知识库", "description": None}
    resp = admin_client.post(f"{kn.router.prefix}/bases", json=body)
    assert resp.status_code == 200
    assert resp.json()["data"]["namespace"] == "cs_vip"

    # 重复创建 → 409 Conflict(3002)
    resp = admin_client.post(f"{kn.router.prefix}/bases", json=body)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == 3002


def test_create_base_invalid_namespace_rejected(admin_client):
    # namespace 仅允许字母/数字/下划线（pydantic pattern）
    resp = admin_client.post(
        f"{kn.router.prefix}/bases", json={"namespace": "bad name!", "display_name": "x"}
    )
    assert resp.status_code == 422


def test_agent_cannot_create_base(staff_client):
    resp = staff_client.post(
        f"{kn.router.prefix}/bases", json={"namespace": "cs_x", "display_name": "x"}
    )
    assert resp.status_code == 403


def test_update_base_not_found_and_success(admin_client, monkeypatch):
    async def fake_update(namespace, **fields):
        if namespace == "missing":
            return None
        # None 字段不应出现在赋值中（KnowledgeBaseRepo.update 跳过 None 语义由 API 层原样透传）
        return SimpleNamespace(
            namespace=namespace,
            display_name=fields.get("display_name"),
            is_active=fields.get("is_active"),
        )

    monkeypatch.setattr(kn.KnowledgeBaseRepo, "update", fake_update)

    resp = admin_client.patch(f"{kn.router.prefix}/bases/missing", json={"is_active": False})
    assert resp.status_code == 404

    resp = admin_client.patch(f"{kn.router.prefix}/bases/cs_order", json={"display_name": "订单库", "is_active": False})
    assert resp.status_code == 200
    assert resp.json()["data"]["display_name"] == "订单库"
    assert resp.json()["data"]["is_active"] is False


def test_delete_base_blocked_when_documents_exist(admin_client, monkeypatch):
    async def fake_get(namespace):
        return SimpleNamespace(namespace=namespace)

    async def fake_count(namespace):
        return 3

    deleted: list[str] = []

    async def fake_delete(namespace):
        deleted.append(namespace)

    monkeypatch.setattr(kn.KnowledgeBaseRepo, "get", fake_get)
    monkeypatch.setattr(kn.DocumentRepo, "count_by_namespace", fake_count)
    monkeypatch.setattr(kn.KnowledgeBaseRepo, "delete", fake_delete)

    resp = admin_client.delete(f"{kn.router.prefix}/bases/cs_order")
    assert resp.status_code == 409
    assert "3" in resp.json()["detail"]["message"]
    assert deleted == []


def test_delete_base_success_when_empty(admin_client, monkeypatch):
    async def fake_get(namespace):
        return SimpleNamespace(namespace=namespace)

    async def fake_count(namespace):
        return 0

    deleted: list[str] = []

    async def fake_delete(namespace):
        deleted.append(namespace)

    monkeypatch.setattr(kn.KnowledgeBaseRepo, "get", fake_get)
    monkeypatch.setattr(kn.DocumentRepo, "count_by_namespace", fake_count)
    monkeypatch.setattr(kn.KnowledgeBaseRepo, "delete", fake_delete)

    resp = admin_client.delete(f"{kn.router.prefix}/bases/cs_order")
    assert resp.status_code == 200
    assert deleted == ["cs_order"]


# ===== 手工 FAQ 条目 =====
def test_create_entry_requires_existing_namespace_and_returns_manual_doc(admin_client, monkeypatch):
    async def fake_get_base(namespace):
        return SimpleNamespace(namespace=namespace) if namespace == "cs_after_sales" else None

    async def fake_create_entry(self, namespace, title, content, uploaded_by):
        return _doc(title=title)

    monkeypatch.setattr(kn.KnowledgeBaseRepo, "get", fake_get_base)
    monkeypatch.setattr(kn.DocumentService, "create_entry", fake_create_entry)

    resp = admin_client.post(
        f"{kn.router.prefix}/entries",
        json={"namespace": "cs_after_sales", "title": "退货说明", "content": "7 天无理由"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == DOC_ID
    assert data["status"] == "ready"
    assert data["chunk_count"] == 2

    resp = admin_client.post(
        f"{kn.router.prefix}/entries",
        json={"namespace": "cs_not_exist", "title": "t", "content": "c"},
    )
    assert resp.status_code == 404


def test_update_entry_only_for_manual(admin_client, monkeypatch):
    updated: list[dict] = []

    async def fake_get(doc_id):
        if str(doc_id) == DOC_ID:
            return _doc()
        return _doc(id=uuid.uuid4(), kind="upload")

    async def fake_update_entry(self, doc_id, title, content):
        updated.append({"doc_id": doc_id, "title": title, "content": content})
        return _doc(title=title)

    monkeypatch.setattr(kn.DocumentRepo, "get", fake_get)
    monkeypatch.setattr(kn.DocumentService, "update_entry", fake_update_entry)

    # manual 条目可编辑
    resp = admin_client.put(f"{kn.router.prefix}/entries/{DOC_ID}", json={"title": "新标题", "content": "新内容"})
    assert resp.status_code == 200
    assert updated[0]["title"] == "新标题"

    # upload 文档不可编辑 → 400
    resp = admin_client.put(f"{kn.router.prefix}/entries/{uuid.uuid4()}", json={"content": "新内容"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001

    # 不存在 → 404
    async def fake_get_none(doc_id):
        return None

    monkeypatch.setattr(kn.DocumentRepo, "get", fake_get_none)
    resp = admin_client.put(f"{kn.router.prefix}/entries/{uuid.uuid4()}", json={"content": "x"})
    assert resp.status_code == 404


# ===== 分块查看（Qdrant 直连层） =====
def test_document_chunks_uses_doc_source_path(staff_client, monkeypatch):
    captured: dict = {}

    async def fake_get(doc_id):
        return _doc()

    async def fake_list_chunks(self, namespace, source_path=None, limit=50, offset=None):
        captured["namespace"] = namespace
        captured["source_path"] = source_path
        captured["limit"] = limit
        return {"chunks": [{"id": "c1", "content": "退货窗口 7 天"}], "next_offset": None}

    monkeypatch.setattr(kn.DocumentRepo, "get", fake_get)
    monkeypatch.setattr(kn.RagService, "list_chunks", fake_list_chunks)

    resp = staff_client.get(f"{kn.router.prefix}/documents/{DOC_ID}/chunks", params={"limit": 10})
    assert resp.status_code == 200
    assert resp.json()["data"]["chunks"][0]["content"] == "退货窗口 7 天"
    # 按文档过滤必须携带 documents.source（与 Qdrant payload source_path 一致）
    assert captured["namespace"] == "cs_after_sales"
    assert captured["source_path"] == qh.manual_source_path(DOC_ID)
    assert captured["limit"] == 10


def test_namespace_chunks_and_not_found(staff_client, monkeypatch):
    async def fake_list_chunks(self, namespace, source_path=None, limit=50, offset=None):
        return {"chunks": [], "next_offset": "off-2"}

    monkeypatch.setattr(kn.RagService, "list_chunks", fake_list_chunks)

    resp = staff_client.get(f"{kn.router.prefix}/chunks", params={"namespace": "cs_default"})
    assert resp.status_code == 200
    assert resp.json()["data"]["next_offset"] == "off-2"

    async def fake_get_none(doc_id):
        return None

    monkeypatch.setattr(kn.DocumentRepo, "get", fake_get_none)
    resp = staff_client.get(f"{kn.router.prefix}/documents/{DOC_ID}/chunks")
    assert resp.status_code == 404


def test_download_raw_success_and_missing(staff_client, monkeypatch):
    import os
    import tempfile

    fd, fpath = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write("# 订单指南\n正文")

    async def fake_get_raw(self, doc_id):
        return fpath, "order.md", "text/markdown"

    monkeypatch.setattr(kn.DocumentService, "get_raw_file", fake_get_raw)
    resp = staff_client.get(f"{kn.router.prefix}/documents/{DOC_ID}/raw")
    assert resp.status_code == 200
    assert "订单指南" in resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")

    async def fake_none(self, doc_id):
        return None

    monkeypatch.setattr(kn.DocumentService, "get_raw_file", fake_none)
    resp = staff_client.get(f"{kn.router.prefix}/documents/{DOC_ID}/raw")
    assert resp.status_code == 404
    assert "重新上传" in resp.json()["detail"]["message"]

    os.remove(fpath)


# ===== Qdrant 过滤条件（纯逻辑，不触网） =====
def test_chunk_filter_and_manual_source_path():
    f = qh._chunk_filter("cs_order")
    keys = {c.key for c in f.must}
    assert keys == {"rag_namespace", "memory_type"}

    f2 = qh._chunk_filter("cs_order", source_path="/kb/a.md")
    keys2 = {c.key for c in f2.must}
    assert "source_path" in keys2

    assert qh.manual_source_path("abc").endswith("abc.md")

    # source_path 为空时拒绝删除，避免误伤整个命名空间
    assert qh.delete_document_chunks("cs_order", None) is False
