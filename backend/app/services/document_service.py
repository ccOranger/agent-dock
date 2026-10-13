"""
DocumentService：上传文档 → 调用 RAGTool.add_document → 归档 DB
"""
import asyncio
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.models.repositories import DocumentRepo
from app.core.config import settings
from app.tools.rag_factory import get_rag_tool, invalidate_rag_tool

# RAGTool.run 返回的是格式化字符串（如 “📊 分块数量: 12”），需从中解析分块数。
_CHUNK_RE = re.compile(r"分块数量[:：]\s*(\d+)")


def _parse_rag_result(result: object) -> tuple[int, str | None]:
    """解析 RAGTool 返回，返回 (chunk_count, error)。

    hello_agents RAGTool.run 不抛异常而是返回文本，失败时以 ❌/⚠️ 开头。
    同时兼容未来可能返回 dict 的情况。
    """
    if isinstance(result, dict):
        err = result.get("error")
        return int(result.get("chunk_count", 0) or 0), (str(err) if err else None)
    text = str(result or "")
    if text.startswith("❌") or text.startswith("⚠️"):
        return 0, text
    m = _CHUNK_RE.search(text)
    return (int(m.group(1)) if m else 0), None


class DocumentService:
    async def upload(self, file: UploadFile, namespace: str, title: str, uploaded_by: str):
        tmp_path = Path(tempfile.gettempdir()) / file.filename
        with tmp_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        doc = await DocumentRepo.create(
            title=title,
            source=str(tmp_path),
            content_type=file.content_type,
            rag_namespace=namespace,
            uploaded_by=uploaded_by,
            status="processing",
            size_bytes=tmp_path.stat().st_size,
        )

        try:
            rag_tool = get_rag_tool(namespace)
            result = await asyncio.to_thread(
                rag_tool.run,
                {
                    "action": "add_document",
                    "file_path": str(tmp_path),
                    "namespace": namespace,
                    "chunk_size": settings.rag_chunk_size,
                    "chunk_overlap": settings.rag_chunk_overlap,
                },
            )
            chunk_count, error = _parse_rag_result(result)
            if error:
                await DocumentRepo.update_status(doc.id, "failed", error_message=error)
                raise RuntimeError(error)
            await DocumentRepo.update_status(doc.id, "ready", chunk_count=chunk_count)
            invalidate_rag_tool(namespace)
        except RuntimeError:
            raise
        except Exception as e:
            await DocumentRepo.update_status(doc.id, "failed", error_message=str(e))
            raise
        finally:
            tmp_path.unlink(missing_ok=True)

        return doc

    async def delete(self, doc_id: str, namespace: str, document_id: str | None = None):
        rag_tool = get_rag_tool(namespace)
        if document_id:
            await asyncio.to_thread(
                rag_tool.run,
                {"action": "delete_document", "document_id": document_id, "namespace": namespace},
            )
        await DocumentRepo.delete(doc_id)
        invalidate_rag_tool(namespace)

    async def reindex(self, namespace: str):
        rag_tool = get_rag_tool(namespace)
        await asyncio.to_thread(rag_tool.run, {"action": "reindex", "namespace": namespace})
        invalidate_rag_tool(namespace)
