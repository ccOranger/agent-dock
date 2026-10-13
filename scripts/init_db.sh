#!/usr/bin/env bash
# 初始化数据库（需本地 PostgreSQL 15 + pgvector）
set -euo pipefail

DB_NAME=${DB_NAME:-cs_db}
DB_USER=${DB_USER:-cs_user}

echo ">> 创建用户与数据库（如已存在则跳过）"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD 'cs_pass';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

echo ">> 安装扩展并执行 init_db.sql"
sudo -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
PGDB_URL="postgresql://${DB_USER}:cs_pass@localhost:5432/${DB_NAME}"
psql "${PGDB_URL}" -f "$(dirname "$0")/init_db.sql"

echo "✅ 数据库初始化完成"
