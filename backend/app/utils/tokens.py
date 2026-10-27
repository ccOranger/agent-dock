"""Token 估算：供应商用量不可得时的兜底口径。

为什么需要估算：``hello_agents`` 的 ``HelloAgentsLLM`` 没有开启
``stream_options.include_usage``，流式响应里既没有 usage 字段也没有可回读的
``last_usage``，因此本项目拿不到"模型自己报的 token 数"。

估算规则（结果写进 ``chat_events.total_tokens``，并用 ``tokens_estimated`` 标记）：

- 装了 ``tiktoken``（见 ``backend/requirements-optional.txt``，Docker 镜像默认不装）
  则按 ``cl100k_base`` 分词计数；
- 没装则按中英混合字符加权：CJK 一字 ≈ 1 token，其余 4 字符 ≈ 1 token；
- ``TOKEN_ESTIMATE_FACTOR`` 供客户按供应商账单整体校准（默认 1.0）。

口径是"可对账的近似值"，不是账单本身。接入真实 usage 后应把
``tokens_estimated`` 置 False，报表里两套数据即可区分。
"""
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

try:  # 可选依赖：缺失时自动降级到字符估算
    import tiktoken
except ImportError:  # pragma: no cover - 取决于环境是否安装
    tiktoken = None  # type: ignore[assignment]

# CJK 及全角标点：按 1 字 ≈ 1 token 计（GPT/国产模型对中文大致如此）
_CJK_RANGES = (
    (0x2E80, 0x2EFF),    # 部首
    (0x3000, 0x303F),    # CJK 标点
    (0x3040, 0x30FF),    # 假名
    (0x3400, 0x4DBF),    # 扩展 A
    (0x4E00, 0x9FFF),    # 基本汉字
    (0xAC00, 0xD7AF),    # 谚文
    (0xF900, 0xFAFF),    # 兼容汉字
    (0xFF00, 0xFF60),    # 全角字符
)

_encoder = None
_encoder_failed = False


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _get_encoder():
    """惰性加载 tiktoken 编码器；离线环境下加载失败则永久降级。"""
    global _encoder, _encoder_failed
    if tiktoken is None or _encoder_failed:
        return None
    if _encoder is None:
        try:
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # noqa: BLE001 - 下载 BPE 失败/离线都按不可用处理
            _encoder_failed = True
            logger.warning(f"tiktoken 不可用，改用字符估算: {exc}")
            return None
    return _encoder


def using_tiktoken() -> bool:
    """当前进程是否用上了真实分词器（否则为字符加权估算）。"""
    return _get_encoder() is not None


def _factor() -> float:
    try:
        return max(float(settings.token_estimate_factor), 0.01)
    except (TypeError, ValueError):
        return 1.0


def estimate_tokens(text: str | None) -> int:
    """单段文本的 token 估算值；空文本返回 0（不参与用量）。"""
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is not None:
        raw = len(encoder.encode(text, disallowed_special=()))
    else:
        cjk = sum(1 for ch in text if _is_cjk(ch))
        # 其余字符按 4 字符 1 token 向上取整（英文单词平均约 1.3 token/词）
        raw = cjk + (len(text) - cjk + 3) // 4
    return max(1, int(round(raw * _factor())))


def estimate_round_tokens(prompt_text: str | None, completion_text: str | None) -> int:
    """一轮对话的用量：输入（拼装后的提示词文本）+ 输出（最终下发的正文）。"""
    return estimate_tokens(prompt_text) + estimate_tokens(completion_text)
