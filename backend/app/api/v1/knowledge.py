"""知识库接口（包装 RAGTool + 管理后台维护能力）"""
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import require_admin, require_staff
from app.core.exceptions import Conflict, NotFound, ParamError
from app.models.repositories import DocumentRepo, KnowledgeBaseRepo
from app.schemas.api import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeEntryCreate,
    KnowledgeEntryUpdate,
    RagSearchRequest,
)
from app.schemas.common import ApiResponse, PaginatedData
from app.services.document_service import DocumentService
from app.services.rag_service import RagService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

ALLOWED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/markdown",
    "text/plain",
}


# ===== 文档上传 / 列表 / 删除 / 重建 =====
@router.post("/documents", response_model=ApiResponse)
async def upload_document(
    file: UploadFile = File(...),
    namespace: str = Form("cs_default"),
    title: str = Form(None),
    user=Depends(require_admin),
):
    if file.content_type not in ALLOWED_TYPES:
        raise ParamError(f"不支持的文件类型: {file.content_type}")
    doc = await DocumentService().upload(
        file=file,
        namespace=namespace,
        title=title or file.filename,
        uploaded_by=str(user.id),
    )
    return ApiResponse(data={"id": str(doc.id), "status": doc.status})


@router.get("/documents", response_model=ApiResponse[PaginatedData[dict]])
async def list_documents(
    namespace: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user=Depends(require_staff),
):
    rows, total = await DocumentRepo.list_all(namespace, page, page_size, status=status, kind=kind)
    items = [
        {
            "id": str(r.id),
            "title": r.title,
            "rag_namespace": r.rag_namespace,
            "kind": r.kind,
            "status": r.status,
            "chunk_count": r.chunk_count,
            "content_type": r.content_type,
            "error_message": r.error_message,
            "original_filename": (r.metadata_ or {}).get("original_filename"),
            "has_archive": bool((r.metadata_ or {}).get("archive_path"))
            and os.path.exists((r.metadata_ or {}).get("archive_path", "")),
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return ApiResponse(data=PaginatedData(items=items, total=total, page=page, page_size=page_size))


@router.get("/documents/{doc_id}/chunks", response_model=ApiResponse[dict])
async def document_chunks(
    doc_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: str | None = None,
    user=Depends(require_staff),
):
    doc = await DocumentRepo.get(doc_id)
    if doc is None:
        raise NotFound("文档不存在")
    result = await RagService().list_chunks(
        doc.rag_namespace, source_path=doc.source, limit=limit, offset=offset
    )
    return ApiResponse(data=result)


@router.get("/chunks", response_model=ApiResponse[dict])
async def namespace_chunks(
    namespace: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    offset: str | None = None,
    user=Depends(require_staff),
):
    result = await RagService().list_chunks(namespace, limit=limit, offset=offset)
    return ApiResponse(data=result)


@router.get("/documents/{doc_id}/raw")
async def download_document_raw(doc_id: uuid.UUID, user=Depends(require_staff)):
    """下载/查看上传文档的原始文件本体（仅新上传且已归档的文档支持）。"""
    info = await DocumentService().get_raw_file(str(doc_id))
    if info is None:
        raise NotFound("该文档无原文（历史文档或已清理），请重新上传")
    path, original_filename, content_type = info
    filename = original_filename or f"document-{doc_id}"
    return FileResponse(
        path,
        media_type=content_type or "application/octet-stream",
        filename=filename,
    )


@router.delete("/documents/{doc_id}", response_model=ApiResponse)
async def delete_document(doc_id: uuid.UUID, user=Depends(require_admin)):
    doc = await DocumentRepo.get(doc_id)
    if doc is None:
        raise NotFound("文档不存在")
    await DocumentService().delete(str(doc.id), doc.rag_namespace, source_path=doc.source)
    return ApiResponse()


@router.post("/documents/{doc_id}/reindex", response_model=ApiResponse)
async def reindex_document(doc_id: uuid.UUID, user=Depends(require_admin)):
    doc = await DocumentRepo.get(doc_id)
    if doc is None:
        raise NotFound("文档不存在")
    await DocumentService().reindex(doc.rag_namespace)
    return ApiResponse()


# ===== 手工 FAQ 文本条目 =====
@router.post("/entries", response_model=ApiResponse)
async def create_entry(body: KnowledgeEntryCreate, user=Depends(require_admin)):
    if await KnowledgeBaseRepo.get(body.namespace) is None:
        raise NotFound("知识库命名空间不存在")
    doc = await DocumentService().create_entry(
        namespace=body.namespace,
        title=body.title,
        content=body.content,
        uploaded_by=str(user.id),
    )
    return ApiResponse(data={"id": str(doc.id), "status": doc.status, "chunk_count": doc.chunk_count})


@router.put("/entries/{doc_id}", response_model=ApiResponse)
async def update_entry(doc_id: uuid.UUID, body: KnowledgeEntryUpdate, user=Depends(require_admin)):
    doc = await DocumentRepo.get(doc_id)
    if doc is None:
        raise NotFound("条目不存在")
    if doc.kind != "manual":
        raise ParamError("仅手工条目支持编辑，上传文档请删除后重新上传")
    updated = await DocumentService().update_entry(
        str(doc.id), title=body.title or doc.title, content=body.content
    )
    return ApiResponse(data={"id": str(updated.id), "status": updated.status, "chunk_count": updated.chunk_count})


# ===== 知识库命名空间 CRUD =====
@router.get("/bases", response_model=ApiResponse[list])
async def list_bases(user=Depends(require_staff)):
    """知识库列表：全局资源，不区分场景归属（绑给哪些 Agent 由 Agent 侧展示）。"""
    rows = await KnowledgeBaseRepo.list_all()
    data = []
    for r in rows:
        data.append(
            {
                "namespace": r.namespace,
                "display_name": r.display_name,
                "description": r.description,
                "is_active": r.is_active,
                "document_count": await DocumentRepo.count_by_namespace(r.namespace),
            }
        )
    return ApiResponse(data=data)


@router.post("/bases", response_model=ApiResponse)
async def create_base(body: KnowledgeBaseCreate, user=Depends(require_admin)):
    if await KnowledgeBaseRepo.get(body.namespace) is not None:
        raise Conflict("命名空间已存在")
    row = await KnowledgeBaseRepo.create(
        namespace=body.namespace,
        display_name=body.display_name,
        description=body.description,
    )
    return ApiResponse(
        data={
            "namespace": row.namespace,
            "display_name": row.display_name,
            "is_active": row.is_active,
        }
    )


@router.patch("/bases/{namespace}", response_model=ApiResponse)
async def update_base(namespace: str, body: KnowledgeBaseUpdate, user=Depends(require_admin)):
    row = await KnowledgeBaseRepo.update(
        namespace,
        display_name=body.display_name,
        description=body.description,
        is_active=body.is_active,
    )
    if row is None:
        raise NotFound("知识库不存在")
    return ApiResponse(
        data={"namespace": row.namespace, "display_name": row.display_name, "is_active": row.is_active}
    )


@router.delete("/bases/{namespace}", response_model=ApiResponse)
async def delete_base(namespace: str, user=Depends(require_admin)):
    if await KnowledgeBaseRepo.get(namespace) is None:
        raise NotFound("知识库不存在")
    count = await DocumentRepo.count_by_namespace(namespace)
    if count > 0:
        raise Conflict(f"该知识库仍有 {count} 个文档，请先删除文档")
    await KnowledgeBaseRepo.delete(namespace)
    return ApiResponse()


@router.post("/search", response_model=ApiResponse)
async def search(body: RagSearchRequest, user=Depends(require_staff)):
    result = await RagService().search(
        body.namespace, body.query, body.top_k, min_score=body.min_score, advanced=body.advanced
    )
    return ApiResponse(data={"results": result})
