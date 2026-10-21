"""场景嵌入组件配置：默认值、归一化与来源校验。

嵌入配置存在 scenes.widget_config（JSONB），后台保存与公开接口读取都走这里，
避免「后台看到的」与「组件拿到的」不是同一份。
"""
import re
from urllib.parse import urlparse

DEFAULT_WIDGET_CONFIG: dict = {
    "title": "",
    "welcome": "您好，请问有什么可以帮您？",
    "theme_color": "#2b5aa0",
    "position": "right",
    "placeholder": "请输入您的问题",
    "show_branding": True,
}

_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_POSITIONS = {"left", "right"}
_TEXT_LIMITS = {"title": 64, "welcome": 500, "placeholder": 100}


def normalize_widget_config(raw: dict | None) -> dict:
    """只保留已知键并做类型/长度收敛；未知键丢弃，避免把后台误填的内容带进组件。"""
    data = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for key, limit in _TEXT_LIMITS.items():
        value = data.get(key)
        if isinstance(value, str):
            out[key] = value.strip()[:limit]
    color = data.get("theme_color")
    if isinstance(color, str) and _COLOR_RE.match(color.strip()):
        out["theme_color"] = color.strip()
    position = data.get("position")
    if isinstance(position, str) and position.strip() in _POSITIONS:
        out["position"] = position.strip()
    if isinstance(data.get("show_branding"), bool):
        out["show_branding"] = data["show_branding"]
    return out


def public_widget_config(scene) -> dict:
    """公开组件配置：默认值 + 场景覆盖；标题缺省用场景展示名。"""
    stored = getattr(scene, "widget_config", None) or {}
    cfg = {**DEFAULT_WIDGET_CONFIG, **normalize_widget_config(stored)}
    cfg["title"] = cfg.get("title") or getattr(scene, "display_name", "") or getattr(
        scene, "name", ""
    )
    return cfg


def normalize_origins(origins: list[str]) -> list[str]:
    """允许来源归一化：只接受 http(s) 源，去掉路径与末尾斜杠，保序去重。"""
    out: list[str] = []
    for raw in origins or []:
        value = (raw or "").strip().rstrip("/")
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"来源必须是 http(s)://host[:port] 形式: {raw}")
        # 只保留 scheme://netloc：粘贴带路径的页面地址时不要让匹配永远失败
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in out:
            out.append(origin)
    return out
