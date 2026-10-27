"""
Qdrant 辅助：直接操作 RAG 向量集合，用于管理后台的分块查看与按文档删除。

背景：hello_agents RAGTool.run() 仅支持 add_document/add_text/ask/search/stats/clear，
不支持 delete_document/reindex，且其底层 doc_id 为内容 md5（外部不可控）。
但每个 chunk 的 payload 稳定包含 rag_namespace / memory_type / source_path，
其中 source_path 与 documents.source 一致，可作为按文档精确删除/过滤的键。

RAGTool 默认 collection_name = "rag_knowledge_base"（见 rag_factory 未显式传入）。
"""
import os

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

RAG_COLLECTION = os.getenv("RAG_QDRANT_COLLECTION", "rag_knowledge_base")
KB_ROOT = os.getenv("RAG_KB_ROOT", "./data/knowledge_bases")

# Qdrant HTTP 客户端超时（秒）：不设置时底层默认无限等待，集群不响应会挂死调用方
_QDRANT_TIMEOUT_SECONDS = 10

_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        url = os.getenv("QDRANT_URL")
        api_key = os.getenv("QDRANT_API_KEY")
        if url:
            _client = QdrantClient(url=url, api_key=api_key or None, timeout=_QDRANT_TIMEOUT_SECONDS)
        else:
            _client = QdrantClient(host="localhost", port=6333, timeout=_QDRANT_TIMEOUT_SECONDS)
    return _client


def _chunk_filter(namespace: str, source_path: str | None = None) -> qmodels.Filter:
    must = [
        qmodels.FieldCondition(key="rag_namespace", match=qmodels.MatchValue(value=namespace)),
        qmodels.FieldCondition(key="memory_type", match=qmodels.MatchValue(value="rag_chunk")),
    ]
    if source_path:
        must.append(
            qmodels.FieldCondition(key="source_path", match=qmodels.MatchValue(value=source_path))
        )
    return qmodels.Filter(must=must)


def manual_source_path(document_id: str) -> str:
    """add_text 生成的 source_path（与 RAGTool 内部 os.path.join 保持一致）。"""
    return os.path.join(KB_ROOT, f"{document_id}.md")


def scroll_chunks(
    namespace: str, source_path: str | None = None, limit: int = 50, offset=None
) -> tuple[list[dict], object]:
    """浏览已索引分块。返回 (chunks, next_offset)。"""
    client = get_qdrant_client()
    points, next_offset = client.scroll(
        collection_name=RAG_COLLECTION,
        scroll_filter=_chunk_filter(namespace, source_path),
        limit=limit,
        offset=offset,
        with_payload=True,
        with_vectors=False,
    )
    chunks = []
    for p in points:
        payload = p.payload or {}
        chunks.append(
            {
                "id": str(p.id),
                "content": payload.get("content", ""),
                "doc_id": payload.get("doc_id"),
                "source_path": payload.get("source_path"),
                "chunk_index": payload.get("start"),
                "heading_path": payload.get("heading_path"),
            }
        )
    return chunks, next_offset


def delete_document_chunks(namespace: str, source_path: str | None) -> bool:
    """按 source_path 精确删除某文档的所有向量；source_path 为空则不操作。"""
    if not source_path:
        return False
    client = get_qdrant_client()
    try:
        client.delete(
            collection_name=RAG_COLLECTION,
            points_selector=qmodels.FilterSelector(filter=_chunk_filter(namespace, source_path)),
            wait=True,
        )
        return True
    except Exception:
        return False


def delete_namespace_chunks(namespace: str) -> bool:
    """删除某命名空间下的全部向量（用于知识库删除）。"""
    client = get_qdrant_client()
    try:
        client.delete(
            collection_name=RAG_COLLECTION,
            points_selector=qmodels.FilterSelector(filter=_chunk_filter(namespace)),
            wait=True,
        )
        return True
    except Exception:
        return False
