"""文本工具：token 估算、重要性评分"""
import re

_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f]")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文按字计，英文按 4 字符≈1 token"""
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + other // 4 + 1


def compute_importance(content: str, metadata: dict, signals: dict) -> float:
    """返回值范围 0~1，辅助业务侧决定是否写入长期记忆"""
    score = 0.3
    if any(k in metadata for k in ("order_id", "ticket_id", "phone")):
        score += 0.2
    score += min(signals.get("sentiment_intensity", 0), 1.0) * 0.2
    score += min(signals.get("turn_count", 0) / 10, 1.0) * 0.1
    if signals.get("repeat_mention", False):
        score += 0.2
    return min(score, 1.0)
