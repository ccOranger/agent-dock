#!/bin/sh
# 后端容器入口：默认先建表/迁移（init_db.sql），再幂等种子，最后起 uvicorn。
# - INIT_ON_START=false：跳过初始化（生产模式，部署时用一次性任务）
# - 传入命令：直接执行，供 docker compose run 做初始化/运维
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

if [ "${INIT_ON_START:-true}" = "false" ]; then
    echo "[entrypoint] INIT_ON_START=false：跳过 init_db/seed_data"
else
    # 数据库容器刚 healthy 时仍可能有短暂抖动，失败重试几次再放弃
    i=1
    while [ "$i" -le 10 ]; do
        if python ../scripts/init_db.py && python ../scripts/seed_data.py; then
            break
        fi
        echo "[entrypoint] init/seed failed (attempt $i/10), retrying in 3s..." >&2
        i=$((i + 1))
        sleep 3
    done

    if [ "$i" -gt 10 ]; then
        echo "[entrypoint] init/seed failed after 10 attempts, aborting" >&2
        exit 1
    fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8000}"
