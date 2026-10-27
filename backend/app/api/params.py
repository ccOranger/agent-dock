"""后台查询参数解析：自由文本输入统一在这里转成强类型，非法值抛 400。

后台列表页的 ID 输入框是自由文本，用户常把日志、报告、CSV 里的 trace_id 直接粘进来。
若不设防地交给 ``uuid.UUID()``，非法值会冒成 500，把「查不到」误报成「服务异常」。
"""
import re
import uuid

from app.core.exceptions import ParamError

# HTTP trace_id 形态：core/logging.py 里 uuid4().hex[:16]；允许粘贴 "trace_id=xxxx"
_TRACE_ID_RE = re.compile(r"^(?:trace[_\-\s]?id\s*=\s*)?([0-9a-fA-F]{16})$", re.IGNORECASE)


def parse_uuid(value: str | None, field: str = "id") -> str | None:
    """校验并规范化 UUID 字符串；非空且非法时抛 ParamError（400）。"""
    if value is None:
        return None
    ref = value.strip()
    if not ref:
        return None
    try:
        return str(uuid.UUID(ref))
    except ValueError:
        raise ParamError(f"{field} 需为 36 位 UUID，收到: {ref}") from None


def split_session_ref(value: str | None) -> tuple[str | None, str | None]:
    """把「会话 ID / 链路 ID」输入拆成 (session_id, trace_id)。

    两种形态不重叠：会话 UUID 是 36 位带连字符，HTTP trace_id 是 16 位十六进制，
    所以可以共用一个输入框——从报告/日志里复制的 trace_id 也能直接查。
    """
    if value is None:
        return None, None
    ref = value.strip().strip("\"'").strip()
    if not ref:
        return None, None
    matched = _TRACE_ID_RE.match(ref)
    if matched:
        return None, matched.group(1).lower()
    return parse_uuid(ref, "session_id"), None


def normalize_trace_id(value: str | None) -> str | None:
    """规范化显式传入的 trace_id：容忍 ``trace_id=`` 前缀、引号与大小写，其余原样返回。"""
    if value is None:
        return None
    ref = value.strip().strip("\"'").strip()
    if not ref:
        return None
    matched = _TRACE_ID_RE.match(ref)
    return matched.group(1).lower() if matched else ref
