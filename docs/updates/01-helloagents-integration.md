# U01 · HelloAgents 0.2.9 集成与依赖适配

> 状态：已落地 ｜ 影响面：后端依赖、启动引导、记忆/RAG/MCP 全部框架调用点、自定义工具写法

## 1. 背景与问题

Spec v1.3 假定 `hello-agents==0.1.0`，并给出若干在 0.2.x 上 **不成立** 的接口：

- `MCPTool(endpoint=..., transport=..., auth=...)` —— 0.2.9 无这些参数；
- `MemoryTool` / `RAGTool` 「自带本地持久化」 —— 实际运行时依赖外部 **Qdrant**（向量）与 **Neo4j**（语义记忆）；
- 直接 `from hello_agents.tools import BaseTool` 并按 `async def run(**kwargs)` 编写自定义工具 —— 0.2.x 只提供同步 `Tool`（构造需 `name/description`，抽象方法 `get_parameters()->List[ToolParameter]`，同步 `run(dict)`）。

升级到 `hello-agents[memory,protocols]>=0.2.4,<=0.2.9` 后，还需要处理一批传递依赖冲突。

## 2. 依赖版本约束（requirements.txt 实况）

| 依赖 | 约束 | 原因 |
|------|------|------|
| `hello-agents` | `[memory,protocols]>=0.2.4,<=0.2.9` | `memory` 提供四层记忆，`protocols` 提供 MCPTool；上界钉死避免 API 漂移 |
| `qdrant-client` | `>=1.9.0,<1.10` | 0.2.9 的 `qdrant_store` 仍 `import SearchRequest`，1.10 已移除该类会导致 `QDRANT_AVAILABLE=False` |
| `bcrypt` | `==4.0.1` | `passlib 1.7.4` 仅兼容 `bcrypt<4.1`，5.x 会导致 `hash_password` 报「password cannot be longer than 72 bytes」 |
| `asyncpg` | `>=0.30.0,<0.31` | 0.29.x 无 cp312/cp313 预编译 wheel |
| `dashscope` | `>=1.20.0` | `EMBED_MODEL_TYPE=dashscope` 时 `DashScopeEmbedding.__init__` 无条件 import dashscope SDK |
| `markitdown[pdf,docx]` | `>=0.1.5` | RAG pipeline 的 PDF/DOCX 增强转换，缺失会回退基础 reader 并刷警告 |
| `fastapi/httpx/pydantic` | 由 `==` 改为 `>=` 下限 | `protocols` 拉取的 `a2a-sdk` 要求更高版本，钉死会解析冲突 |

## 3. 运行时服务拓扑

```
Backend ── PostgreSQL ── 业务归档（sessions/messages/memories/documents/tickets/traces/...）
       ├─ Redis ──────── 工作记忆 / 缓存
       ├─ Qdrant ─────── MemoryTool 向量层 + RAGTool 知识库向量（RAG 不落 PG）
       └─ Neo4j(可选) ── semantic 语义记忆；未部署时不启用该层
```

- **Qdrant**：`QDRANT_URL / QDRANT_API_KEY / QDRANT_COLLECTION / QDRANT_DISTANCE`；`QDRANT_URL` 留空回退本地 `localhost:6333`。
- **Neo4j**：semantic 层硬依赖。未部署时通过 `MEMORY_TYPES` 排除 semantic，否则启动即连接失败中断。

## 4. 记忆层可配置（config.bootstrap / memory_factory / rag_factory）

`Settings.memory_types` 默认 `working,episodic,perceptual`（**不含 semantic**）。所有构造 `MemoryTool` 的位置统一读取 `settings.memory_type_list`：

```python
# core/bootstrap.py & tools/memory_factory.py
MemoryTool(user_id=..., memory_types=settings.memory_type_list)
```

部署 Neo4j 后，把 `MEMORY_TYPES` 改为 `working,episodic,semantic,perceptual` 即启用语义记忆，无需改代码。

## 5. BaseTool 适配层（tools/custom/base.py）

为保留「类属性 + async run(**kwargs)」的自定义工具写法，对 0.2.x 的同步 `Tool` 做一层薄适配：

```python
class BaseTool(Tool):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}     # JSON-Schema 风格

    def __init__(self):
        super().__init__(name=self.name, description=self.description)

    def get_parameters(self) -> List[ToolParameter]:
        # 把子类 parameters.properties 转成 ToolParameter 列表
        ...

    def run(self, parameters: dict):     # 占位；子类以 async run(**kwargs) 覆盖
        raise NotImplementedError
```

子类只需声明 `name/description/parameters` 并实现 `async def run(**kwargs)`；真正的异步执行由 [U09](09-async-tool-execution.md) 的编排器工具执行器负责。

## 6. MCPTool 的正确用法（0.2.9）

0.2.9 的 `MCPTool` 通过 `server_command` 接受多形态入参（由内部 `MCPClient._prepare_server_source` 识别传输类型）：

```python
tool = MCPTool(name=cfg["name"], server_command=server_source)
tool.expandable = True          # 关键：默认 False 会被当作单个“元工具”，模型无法调用真实函数
registry.register_tool(tool)    # expandable=True 时展开为每个远端工具的独立 MCPWrappedTool
```

`server_source` 形态：
- **stdio**：`[解释器, 脚本路径, *args]`（列表）；
- **sse / http**：`{"transport": "sse"|"http", "url": ..., "headers": ..., "auth": ...}`（字典）。

详见 [U05](05-stdio-mcp-demo.md)。

## 7. 环境加载注意（core/config.py）

hello_agents 框架用 `os.getenv()` 直接读取 `LLM_ / EMBED_ / RAG_ / QDRANT_` 等变量，而 `pydantic-settings` **不会写入 `os.environ`**。因此 `config.py` 在实例化前显式 `load_dotenv(".env")`，且必须在框架读取之前执行。

## 8. 验证

- `backend/tests/test_hello_agents_integration.py`：全栈（MemoryTool + RAGTool + ToolRegistry + SimpleAgent）冒烟。
- `passlib/bcrypt`：`test_security.py::hash_password` 自检。
- 手动：启动日志出现「工具 → 注册 → Agent」三步且无 Qdrant/Neo4j 连接报错。

## 9. 已知边界

- semantic 记忆默认关闭（无 Neo4j 环境）；`ContextEngineer.persist_after_run` 写入 `episodic`（含结构化信号时标 `semantic` 类型入库，但运行时记忆层仍以配置为准）。
- RAGTool 高级检索（HyDE+MQE）用于后台检索测试；对话链路为保证延迟使用纯向量检索（`advanced=False`）。
