#!/bin/sh
# 后端容器入口：迁移 → 首启初始化/种子 → uvicorn。
# - MIGRATE_ON_START=true：启动前执行增量迁移（存量库升级用，默认关闭）
# - INIT_ON_START=false：跳过初始化（生产模式，部署时用一次性任务）
# - 传入命令：直接执行，供 docker compose run 做初始化/运维
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# 数据库容器刚 healthy 时仍可能有短暂抖动，失败重试几次再放弃
retry() {
    i=1
    while [ "$i" -le 10 ]; do
        if "$@"; then
            return 0
        fi
        echo "[entrypoint] $* failed (attempt $i/10), retrying in 3s..." >&2
        i=$((i + 1))
        sleep 3
    done
    echo "[entrypoint] $* failed after 10 attempts, aborting" >&2
    exit 1
}

if [ "${MIGRATE_ON_START:-false}" = "true" ]; then
    # 空库时 migrate 会直接用 init_db.sql 建到最新结构，无需额外处理
    retry python -m app.migrate
fi

if [ "${INIT_ON_START:-true}" = "false" ]; then
    echo "[entrypoint] INIT_ON_START=false：跳过 init_db/seed_data"
else
    retry sh -c 'python ../scripts/init_db.py && python ../scripts/seed_data.py'
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8000}"
