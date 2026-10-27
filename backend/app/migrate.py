"""``python -m app.migrate`` 的命令行入口。

实现放在 ``app/core/migrate.py``（与其他基础能力同处 core，便于被 schema 守卫、
校验脚本与测试直接 import）。这里只提供短路径入口，不放任何逻辑：
容器与文档统一写 ``python -m app.migrate``，不必暴露内部目录结构。
"""
import asyncio

from app.core.migrate import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
