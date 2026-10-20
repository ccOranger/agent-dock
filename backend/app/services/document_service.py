"""
DocumentService：上传文档 → 调用 RAGTool.add_document → 归档 DB
"""
import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import ParamError
from app.models.repositories import DocumentRepo
from app.services import qdrant_helper
from app.tools.rag_factory import get_rag_tool, invalidate_rag_tool

# RAGTool.run 返回的是格式化字符串（如 “📊 分块数量: 12”），需从中解析分块数。
_CHUNK_RE = re.compile(r"分块数量[:：]\s*(\d+)")


def _archive_path(namespace: str, doc_id: str, filename: str | None) -> Path:
    """归档原始文件的落盘路径：<archive_root>/<namespace>/<doc_id><ext>，与 doc.id 绑定便于清理。"""
    ext = os.path.splitext(filename or "")[1][:16]
    return Path(settings.rag_doc_archive_root) / namespace / f"{doc_id}{ext}"


def _resolve_archive(doc) -> str | None:
    """从文档 metadata_ 中取出已存在磁盘的归档路径，不存在则返回 None。"""
    meta = getattr(doc, "metadata_", None) or {}
    path = meta.get("archive_path")
    if path and os.path.exists(path):
        return path
    return None


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


def _save_upload(file: UploadFile, max_bytes: int) -> Path:
    """把上传内容写到随机临时文件，并限制体积（超限抛 ParamError）。

    - 用 mkstemp 生成随机名：原始文件名可能含 ../（路径穿越）或与他人同名（并发覆盖）；
    - 分块拷贝并累计大小：落盘阶段设硬上限，超限立即清理，不把超大文件留在磁盘上。
    """
    suffix = Path(file.filename or "").suffix[:16]
    fd, name = tempfile.mkstemp(prefix="cs_upload_", suffix=suffix)
    tmp_path = Path(name)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ParamError(f"文件大小超过上限（{max_bytes // (1024 * 1024)} MB）")
                out.write(chunk)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


class DocumentService:
    async def upload(self, file: UploadFile, namespace: str, title: str, uploaded_by: str):
        # 随机临时文件名 + 大小上限：规避路径穿越、同名并发覆盖与超大文件 DoS
        max_bytes = settings.upload_max_size_mb * 1024 * 1024
        # 请求体在进入 handler 前已被 multipart 解析器完整落盘，尽早按声明大小拒绝，
        # 避免超大文件先占满磁盘、再被分块计数拒掉时峰值磁盘占用翻倍
        if file.size is not None and file.size > max_bytes:
            raise ParamError(f"文件大小超过上限（{settings.upload_max_size_mb} MB）")
        tmp_path = await asyncio.to_thread(_save_upload, file, max_bytes)

        try:
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
                # 索引成功后归档原始文件（供后台查看/下载），与向量 source_path 解耦，失败不阻断入库
                archive_info: dict = {"original_filename": file.filename}
                try:
                    arc = _archive_path(namespace, str(doc.id), file.filename)
                    arc.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(tmp_path, arc)
                    archive_info["archive_path"] = str(arc)
                except Exception:  # noqa: BLE001
                    pass
                await DocumentRepo.update_status(
                    doc.id, "ready", chunk_count=chunk_count, metadata_=archive_info
                )
                doc.metadata_ = archive_info
                invalidate_rag_tool(namespace)
            except RuntimeError:
                raise
            except Exception as e:
                await DocumentRepo.update_status(doc.id, "failed", error_message=str(e))
                raise
            return doc
        finally:
            # 建库失败/请求取消也要清理：每份上传都是唯一随机名，残留会持续累积
            tmp_path.unlink(missing_ok=True)

    async def delete(self, doc_id: str, namespace: str, source_path: str | None = None):
        """删除文档：先按 source_path 精确清理 Qdrant 向量，再删 DB 行，并清理归档原文。"""
        await asyncio.to_thread(qdrant_helper.delete_document_chunks, namespace, source_path)
        doc = await DocumentRepo.get(doc_id)
        archived = _resolve_archive(doc) if doc else None
        await DocumentRepo.delete(doc_id)
        if archived:
            try:
                os.remove(archived)
            except OSError:
                pass
        invalidate_rag_tool(namespace)

    async def get_raw_file(self, doc_id: str) -> tuple[str, str | None, str | None] | None:
        """返回 (归档绝对路径, 原始文件名, content_type)；无归档/历史文档返回 None。"""
        doc = await DocumentRepo.get(doc_id)
        if doc is None:
            return None
        archived = _resolve_archive(doc)
        if not archived:
            return None
        meta = doc.metadata_ or {}
        return archived, meta.get("original_filename"), doc.content_type

    async def create_entry(self, namespace: str, title: str, content: str, uploaded_by: str):
        """手工 FAQ 文本条目：以 document_id=新建文档 UUID 走 add_text 即时入库。"""
        # 先建档拿到 UUID（source 使用 add_text 内部可预测的临时 md 路径，用于后续向量删除）
        doc = await DocumentRepo.create(
            title=title,
            rag_namespace=namespace,
            content_type="text/markdown",
            kind="manual",
            uploaded_by=uploaded_by,
            status="processing",
            size_bytes=len(content.encode("utf-8")),
        )
        source_path = qdrant_helper.manual_source_path(str(doc.id))
        try:
            rag_tool = get_rag_tool(namespace)
            result = await asyncio.to_thread(
                rag_tool.run,
                {
                    "action": "add_text",
                    "text": content,
                    "document_id": str(doc.id),
                    "namespace": namespace,
                    "chunk_size": settings.rag_chunk_size,
                    "chunk_overlap": settings.rag_chunk_overlap,
                },
            )
            chunk_count, error = _parse_rag_result(result)
            if error:
                await DocumentRepo.update_status(doc.id, "failed", error_message=error)
                raise RuntimeError(error)
            # 回写可预测的 source_path，用于后续按向量删除
            await DocumentRepo.update_status(doc.id, "ready", chunk_count=chunk_count, source=source_path)
            doc.source = source_path
            doc.status = "ready"
            doc.chunk_count = chunk_count
            invalidate_rag_tool(namespace)
        except RuntimeError:
            raise
        except Exception as e:
            await DocumentRepo.update_status(doc.id, "failed", error_message=str(e))
            raise
        return doc

    async def update_entry(self, doc_id: str, title: str, content: str):
        """编辑手工条目：先删旧向量（同一 document_id 产生的 source_path），再重新 add_text。"""
        doc = await DocumentRepo.get(doc_id)
        if doc is None:
            raise RuntimeError("文档不存在")
        namespace = doc.rag_namespace
        source_path = doc.source or qdrant_helper.manual_source_path(str(doc.id))
        # 清理旧向量
        await asyncio.to_thread(qdrant_helper.delete_document_chunks, namespace, source_path)
        # 重新入库（同一 document_id → 同一 source_path）
        rag_tool = get_rag_tool(namespace)
        result = await asyncio.to_thread(
            rag_tool.run,
            {
                "action": "add_text",
                "text": content,
                "document_id": str(doc.id),
                "namespace": namespace,
                "chunk_size": settings.rag_chunk_size,
                "chunk_overlap": settings.rag_chunk_overlap,
            },
        )
        chunk_count, error = _parse_rag_result(result)
        if error:
            await DocumentRepo.update_status(doc.id, "failed", error_message=error)
            raise RuntimeError(error)
        await DocumentRepo.update_status(
            doc.id, "ready", chunk_count=chunk_count, source=source_path, title=title,
            size_bytes=len(content.encode("utf-8")),
        )
        invalidate_rag_tool(namespace)
        return await DocumentRepo.get(doc_id)

    async def reindex(self, namespace: str):
        rag_tool = get_rag_tool(namespace)
        await asyncio.to_thread(rag_tool.run, {"action": "reindex", "namespace": namespace})
        invalidate_rag_tool(namespace)
