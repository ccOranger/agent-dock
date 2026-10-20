# 后端镜像：FastAPI + HelloAgents（含 MCP stdio 所需的 Python 运行时）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 非 root 运行；数据目录先在镜像内建好并授权，命名卷首次挂载会继承此属主
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/backend/data/knowledge_bases /app/backend/data/doc_archives /app/backend/memory_data \
    && chown -R appuser:appuser /app

# 依赖单独一层：requirements.txt 未变化时可复用构建缓存
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY scripts/seed_data.py scripts/init_db.py scripts/init_db.sql /app/scripts/
COPY docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh && chown -R appuser:appuser /app

USER appuser
WORKDIR /app/backend
EXPOSE 8000

# 启动期要连 Qdrant/记忆初始化，给 90s 宽限
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["/usr/local/bin/backend-entrypoint.sh"]
