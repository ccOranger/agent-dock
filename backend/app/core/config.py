"""应用配置：基于 pydantic-settings，从环境变量 / .env 加载"""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# hello_agents 框架通过 os.getenv() 直接读取 LLM_/EMBED_/RAG_ 等环境变量，
# 而 pydantic-settings 不会写入 os.environ，故在此显式加载 .env 到进程环境。
# （必须在实例化 Settings / 框架读取之前执行）
load_dotenv(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ===== 应用 =====
    app_env: str = "development"
    app_name: str = "smart-cs"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # ===== CORS（逗号分隔的允许来源；默认放行客户端前端 5173 与管理后台 5174） =====
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # ===== HelloAgents LLM =====
    llm_model_id: str = "gpt-4o"
    llm_api_key: str = "sk-xxx"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout: int = 60
    llm_max_retries: int = 2

    # ===== Embedding（hello_agents 框架直接读取环境变量，此处仅作声明） =====
    embed_model_type: str = "dashscope"
    embed_model_name: str = ""
    embed_api_key: str = ""
    embed_base_url: str = ""

    # ===== RAG =====
    rag_kb_root: str = "./data/knowledge_bases"
    # 上传文档原始文件的归档目录（用于后台查看/下载原文，与向量 source_path 解耦）
    rag_doc_archive_root: str = "./data/doc_archives"
    rag_default_namespace: str = "cs_default"
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64
    rag_top_k: int = 5
    rag_min_score: float = 0.3

    # ===== 数据库 =====
    database_url: str = "postgresql+asyncpg://cs_user:cs_pass@localhost:5432/cs_db"
    db_pool_size: int = 20
    db_max_overflow: int = 10

    # ===== Redis =====
    redis_url: str = "redis://localhost:6379/0"
    working_memory_ttl: int = 1800

    # ===== 向量库 Qdrant（hello_agents 记忆/RAG 底层读取环境变量） =====
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "hello_agents_vectors"

    # ===== 记忆层（逗号分隔） =====
    # semantic 依赖 Neo4j；未部署 Neo4j 时不要包含 semantic，否则启动会因连接失败而中断。
    # 部署 Neo4j 后，将 MEMORY_TYPES 改为 working,episodic,semantic,perceptual 即可启用语义记忆。
    memory_types: str = "working,episodic,perceptual"

    @property
    def memory_type_list(self) -> list[str]:
        return [t.strip() for t in self.memory_types.split(",") if t.strip()]

    # ===== 鉴权 =====
    jwt_secret: str = "change_me_in_prod"
    jwt_algorithm: str = "HS256"
    access_token_ttl: int = 7200
    refresh_token_ttl: int = 604800

    # ===== 记忆阈值 =====
    memory_importance_threshold: float = 0.35
    memory_access_threshold: int = 3
    memory_decay_half_life_days: int = 30

    # ===== 上下文预算 =====
    context_max_tokens: int = 8000
    context_system_reserve: int = 800
    context_history_reserve: int = 2000

    # ===== 路由 =====
    router_model: str = "deepseek-flash"
    router_confidence_threshold: float = 0.5
    # 单条消息最多分发的业务意图数（多意图串行执行上限）
    router_max_intents: int = 2

    # ===== 链路追踪（request_traces）=====
    trace_enabled: bool = True
    # 工具入参/返回是否入库（含客户数据）：只存截断后的文本，关闭则仅存工具名与耗时
    trace_store_payload: bool = True
    trace_payload_max_chars: int = 2000
    # 正常应答的采样率 0~1；转人工/出错的请求永远全量记录
    trace_sample_rate: float = 1.0
    # 保留天数，供清理脚本使用
    trace_retention_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
