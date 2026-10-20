#!/bin/sh
# 后端容器入口：先建表/迁移（init_db.sql），再幂等种子（默认 Agent/工具/话术 + 初始管理员），最后起 uvicorn。
set -eu

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

exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8000}"
