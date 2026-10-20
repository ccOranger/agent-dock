"""清理过期请求链路（request_traces）

链路表按轮次增长，一轮带完整 spans 时间线约几 KB，必须设保留期。
默认取 TRACE_RETENTION_DAYS（.env 可配），异常/转人工轮不做特殊豁免：
排查窗口本身也有限，超期一律清理。

用法（backend 目录）：
    .\\.venv\\Scripts\\python.exe ..\\scripts\\cleanup_traces.py            # 按配置保留期
    .\\.venv\\Scripts\\python.exe ..\\scripts\\cleanup_traces.py --days 7   # 只保留 7 天
    .\\.venv\\Scripts\\python.exe ..\\scripts\\cleanup_traces.py --dry-run  # 只看会删多少
"""
import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import engine  # noqa: E402
from app.models.entities import RequestTrace  # noqa: E402


async def main(days: int, dry_run: bool) -> None:
    cutoff = RequestTrace.created_at < datetime.now(UTC) - timedelta(days=max(1, days))
    async with engine.begin() as conn:
        doomed = (await conn.execute(select(func.count()).select_from(RequestTrace).where(cutoff))).scalar_one()
        print(f">> 保留 {days} 天，待清理 {doomed} 条")
        if dry_run or not doomed:
            return
        result = await conn.execute(delete(RequestTrace).where(cutoff))
        print(f"[OK] 已清理 {result.rowcount or 0} 条链路记录")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理过期请求链路")
    parser.add_argument("--days", type=int, default=settings.trace_retention_days, help="保留天数")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.dry_run))
