# U08 · 知识库管理增强

> 状态：已落地 ｜ 影响面：knowledge_bases 表、文档上传/归档、手工 FAQ 条目、分块浏览、高级检索、多 namespace 检索

## 1. 背景与问题

Spec 只给了「上传 → RAGTool.add_document → 归档 DB」的最小链路。运营需要：命名空间可视化管理、原文可回看/下载、无需上传文件也能录 FAQ、能看到已索引分块、检索可测（含高级检索），以及通用问答 Agent 能自动覆盖后台新建的知识库。

## 2. knowledge_bases 表（namespace 主表）

`models/entities.py::KnowledgeBase`：`namespace`(PK) / `display_name` / `description` / `is_active`。
`init_db.sql` 预置 `cs_default / cs_order / cs_after_sales`（与代码 Agent 的 `rag_namespace` 对齐）。

CRUD（api/v1/knowledge.py，admin 写、staff 读）：

| 端点 | 说明 |
|------|------|
| `GET /knowledge/bases` | 列表（附每库 document_count） |
| `POST /knowledge/bases` | 新建命名空间（`^[A-Za-z0-9_]+$`，重名 Conflict） |
| `PATCH /knowledge/bases/{ns}` | 改展示名/描述/启停 |
| `DELETE /knowledge/bases/{ns}` | 删除（仍有文档时 Conflict，需先删文档） |

## 3. 文档上传与原文归档（services/document_service.py）

`Document.kind ∈ {upload, manual}`。上传流程：

1. 落临时文件 → `DocumentRepo.create(status=processing)`；
2. `RAGTool.run({action:add_document, file_path, namespace, chunk_size, chunk_overlap})`（`asyncio.to_thread`，不阻塞事件循环）；
3. `_parse_rag_result`：hello_agents 的 `RAGTool.run` 返回 **给人看的文本**（如「📊 分块数量: 12」），失败以 ❌/⚠️ 开头 —— 正则提取 `chunk_count`，失败标 `failed` 并抛错；
4. **原文归档**（成功才做，失败不阻断入库）：复制到 `<RAG_DOC_ARCHIVE_ROOT>/<namespace>/<doc_id><ext>`，路径与 `original_filename` 写入 `documents.metadata`；与向量 `source_path` 解耦；
5. `update_status(ready, chunk_count, metadata)` + `invalidate_rag_tool(namespace)`。

下载原文：`GET /knowledge/documents/{id}/raw`（FileResponse）；无归档（历史文档/已清理）返回 `NotFound` 提示重传。列表以 `has_archive` 标注是否可下载。

## 4. 手工 FAQ 条目（kind=manual）

无需上传文件，直接录入文本：

| 端点 | 说明 |
|------|------|
| `POST /knowledge/entries` | 建档拿 UUID → `RAGTool.add_text(document_id=UUID,...)` 即时入库；`source` 记为可预测的 `qdrant_helper.manual_source_path(doc_id)` 以便后续按向量删除 |
| `PUT /knowledge/entries/{id}` | 编辑（仅 manual）：先 `delete_document_chunks(namespace, source_path)` 清旧向量，再同 document_id 重新 add_text |

## 5. 分块浏览（直连 Qdrant）

向量的真相在 Qdrant，不在 PostgreSQL。后台分块浏览经 `services/qdrant_helper.py`：

| 端点 | 说明 |
|------|------|
| `GET /knowledge/documents/{id}/chunks` | 按该文档 `source_path` 过滤，`scroll_chunks` 游标翻页 |
| `GET /knowledge/chunks?namespace=` | 整库分块浏览 |

删除文档：先 `qdrant_helper.delete_document_chunks(namespace, source_path)` 精确清向量，再删 DB 行、清归档原文、`invalidate_rag_tool`。

## 6. 检索（services/rag_service.py）

调 RAGTool 内部 pipeline（非 `tool.run("search")`，后者返回给人看的字符串）：

- `search(namespace, query, top_k, min_score, advanced)`：
  - `advanced=True` → `search_advanced(enable_mqe, enable_hyde, score_threshold)`（后台检索测试用）；
  - `advanced=False` → 纯向量 `search`（**对话链路为保证 TTFT 用此模式**）；
  - 返回结构化 `{content, score, source, source_name, heading_path}`。
- `POST /knowledge/search`：后台检索测试入口（默认 advanced）。

## 7. 多 namespace 检索与自动覆盖（orchestrator._resolve_rag_namespaces）

`ContextEngineer.build` 接收 `rag_namespaces: list[str]`，跨库检索后按 `score` 归并取 top_k。Agent 侧：

- `search_all_knowledge_bases=True`（如 KnowledgeAgent）→ 覆盖 **所有启用中的知识库**（`KnowledgeBaseRepo.list_all` 过滤 `is_active`）+ 自身默认空间去重前置，使后台新建库（如 cs_pgsql）**无需改代码即可被命中**；
- 否则仅用 Agent 自身 `rag_namespace`。

`ContextEngineer._recall_rag` 逐 namespace 检索并记 `rag_namespaces`/`chunk_count` 到链路（[U04](04-request-tracing.md)）。

## 8. 前端（admin-frontend/views/KnowledgeBaseView.vue）

命名空间管理、文档上传/列表/下载/删除、手工条目编辑、分块浏览、检索测试；`components/DocumentUploader.vue` 拖拽上传。

## 9. 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `RAG_KB_ROOT` | `./data/knowledge_bases` | RAGTool 知识库根目录 |
| `RAG_DOC_ARCHIVE_ROOT` | `./data/doc_archives` | 原文归档目录（与向量解耦，供后台下载） |
| `RAG_CHUNK_SIZE/OVERLAP/TOP_K/MIN_SCORE` | 512/64/5/0.3 | 分块与检索阈值 |

## 10. 验证

`tests/test_knowledge_bases.py`、`tests/fixtures/kb/`。

## 11. 已知修复（历史坑）

RAG 曾出现「检索恒空、chunk_count 恒 0」：根因是 namespace 未透传到 pipeline 且 `run` 返回字符串未解析。现通过显式传 `namespace` + `_parse_rag_result` 解析文本 + 结构化 `search` 返回解决。
