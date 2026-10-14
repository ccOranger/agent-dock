#!/usr/bin/env bash
# 初始化数据库（需本地 PostgreSQL 13+；schema 不依赖 pgvector / uuid-ossp）
set -euo pipefail

DB_NAME=${DB_NAME:-cs_db}
DB_USER=${DB_USER:-cs_user}

echo ">> 创建用户与数据库（如已存在则跳过）"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD 'cs_pass';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

echo ">> 执行 init_db.sql（pg_trgm 扩展已在脚本内幂等创建）"
PGDB_URL="postgresql://${DB_USER}:cs_pass@localhost:5432/${DB_NAME}"
psql "${PGDB_URL}" -f "$(dirname "$0")/init_db.sql"

echo "✅ 数据库初始化完成"
