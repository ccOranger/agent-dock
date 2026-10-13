"""知识库接口（包装 RAGTool）"""
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.deps import require_admin, require_staff
from app.core.exceptions import NotFound
from app.models.repositories import DocumentRepo
from app.schemas.api import RagSearchRequest
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


@router.post("/documents", response_model=ApiResponse)
async def upload_document(
    file: UploadFile = File(...),
    namespace: str = Form("cs_default"),
    title: str = Form(None),
    user=Depends(require_admin),
):
    if file.content_type not in ALLOWED_TYPES:
        from app.core.exceptions import ParamError

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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user=Depends(require_staff),
):
    rows, total = await DocumentRepo.list_all(namespace, page, page_size)
    items = [
        {
            "id": str(r.id),
            "title": r.title,
            "rag_namespace": r.rag_namespace,
            "status": r.status,
            "chunk_count": r.chunk_count,
            "content_type": r.content_type,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return ApiResponse(data=PaginatedData(items=items, total=total, page=page, page_size=page_size))


@router.delete("/documents/{doc_id}", response_model=ApiResponse)
async def delete_document(doc_id: uuid.UUID, user=Depends(require_admin)):
    doc = await DocumentRepo.get(doc_id)
    if doc is None:
        raise NotFound("文档不存在")
    await DocumentService().delete(str(doc.id), doc.rag_namespace, document_id=str(doc.id))
    return ApiResponse()


@router.post("/documents/{doc_id}/reindex", response_model=ApiResponse)
async def reindex_document(doc_id: uuid.UUID, user=Depends(require_admin)):
    doc = await DocumentRepo.get(doc_id)
    if doc is None:
        raise NotFound("文档不存在")
    await DocumentService().reindex(doc.rag_namespace)
    return ApiResponse()


@router.post("/search", response_model=ApiResponse)
async def search(body: RagSearchRequest, user=Depends(require_staff)):
    result = await RagService().search(body.namespace, body.query, body.top_k)
    return ApiResponse(data={"results": result})
