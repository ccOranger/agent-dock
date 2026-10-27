#!/bin/sh
# 备份 smart-cs 持久化数据（在部署机仓库根目录执行，要求服务处于运行状态）：
#   postgres.dump      PostgreSQL 自定义格式备份（pg_restore）
#   qdrant-*.snapshot  Qdrant 全量快照
#   backenddata/       知识库、文档归档等
#   memorydata/        hello-agents 记忆 SQLite
#   redis.rdb          Redis RDB 快照
#
# 用法：
#   bash scripts/backup.sh
#   COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml" bash scripts/backup.sh
# 可选环境变量：
#   BACKUP_ROOT（默认 ./backups）
#   BACKUP_RETAIN_DAYS（默认 7；0 表示不清理）
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

# 读取项目配置（POSTGRES_*、REDIS_PASSWORD 等），与 Compose 使用同一份 .env
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

BACKUP_ROOT=${BACKUP_ROOT:-./backups}
RETAIN_DAYS=${BACKUP_RETAIN_DAYS:-7}
if [ "$BACKUP_ROOT" = "/" ]; then
    echo "[backup] BACKUP_ROOT 不能是 /" >&2
    exit 1
fi
case "$RETAIN_DAYS" in
    ''|*[!0-9]*)
        echo "[backup] BACKUP_RETAIN_DAYS 必须是非负整数" >&2
        exit 1
        ;;
esac
TS=$(date +%Y%m%d-%H%M%S)
DEST="$BACKUP_ROOT/$TS"
mkdir -p "$DEST"

echo "[backup] 目标目录：$DEST"
echo "[backup] Compose 文件：${COMPOSE_FILE:-docker-compose.yml}"

# 1. PostgreSQL：自定义格式，便于 pg_restore 选择性恢复
PG_USER=$(docker compose exec -T postgres printenv POSTGRES_USER)
PG_DB=$(docker compose exec -T postgres printenv POSTGRES_DB)
docker compose exec -T postgres \
    pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --format=custom \
    > "$DEST/postgres.dump"
echo "[backup] postgres 完成（$PG_DB）"

# 2. Qdrant 全量快照：由 backend 调用 API（无需向宿主机暴露 6333），再复制出来
SNAP_NAME=$(docker compose exec -T backend python - <<'PY'
import os

import httpx

base = os.environ["QDRANT_URL"].rstrip("/")
headers = {}
if os.environ.get("QDRANT_API_KEY"):
    headers["api-key"] = os.environ["QDRANT_API_KEY"]
resp = httpx.post(f"{base}/snapshots", headers=headers, timeout=300)
resp.raise_for_status()
print(resp.json()["result"]["name"])
PY
)
docker compose cp "qdrant:/qdrant/snapshots/$SNAP_NAME" "$DEST/qdrant-$SNAP_NAME"
# 宿主机已保留副本，清理 Qdrant 内快照避免目录无限增长（失败不影响本次备份）
docker compose exec -T -e SNAPSHOT_NAME="$SNAP_NAME" backend python - <<'PY' || echo "[backup] 警告：Qdrant 快照清理失败，可手动删除"
import os

import httpx

base = os.environ["QDRANT_URL"].rstrip("/")
headers = {}
if os.environ.get("QDRANT_API_KEY"):
    headers["api-key"] = os.environ["QDRANT_API_KEY"]
resp = httpx.delete(f"{base}/snapshots/{os.environ['SNAPSHOT_NAME']}", headers=headers, timeout=60)
resp.raise_for_status()
PY
echo "[backup] qdrant 完成（$SNAP_NAME）"

# 3. 业务数据：bind mount / named volume 都通过容器复制，避免宿主机路径差异
mkdir -p "$DEST/backenddata" "$DEST/memorydata"
docker compose cp backend:/app/backend/data/. "$DEST/backenddata"
docker compose cp backend:/app/backend/memory_data/. "$DEST/memorydata"
echo "[backup] backenddata/memorydata 完成"

# 4. Redis：用 RDB 快照，避免直接拷贝运行中的 AOF 得到不一致状态
if [ -n "${REDIS_PASSWORD:-}" ]; then
    docker compose exec -T -e REDISCLI_AUTH="$REDIS_PASSWORD" redis \
        redis-cli --rdb /tmp/smart-cs-backup.rdb >/dev/null
else
    docker compose exec -T redis redis-cli --rdb /tmp/smart-cs-backup.rdb >/dev/null
fi
docker compose cp redis:/tmp/smart-cs-backup.rdb "$DEST/redis.rdb"
echo "[backup] redis 完成"

# 5. 清单与保留策略
{
    echo "created_at=$TS"
    echo "postgres_db=$PG_DB"
    echo "qdrant_snapshot=$SNAP_NAME"
    echo "compose_file=${COMPOSE_FILE:-docker-compose.yml}"
} > "$DEST/manifest.txt"

if [ "$RETAIN_DAYS" -gt 0 ]; then
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$RETAIN_DAYS" -exec rm -rf {} +
fi

echo "[backup] 完成："
du -sh "$DEST"
