"""SSE 事件格式化与解析工具"""
import json


def sse_event(event: str, data: dict) -> str:
    """格式化一条 SSE 事件：event: <type>\\ndata: <json>\\n\\n"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

SSE_PING = ": ping\n\n"
