"""高频固定话术（硬模板）：命中则逐字下发，不进路由也不走模型生成

为什么不放进提示词：
- 这三类回答内容恒定，交给模型复述模板会出现"改写跑偏 / 多嘴泄露内部结构 / 每次措辞不同"；
- 它们发生在路由之前，命中即直接应答，省掉路由 LLM 调用 + Agent 构建（MCP 建连、RAG/记忆检索）
  的数秒空窗，首字延迟从秒级降到毫秒级。

规则来源：只读 DB 表 canned_scripts（后台「固定话术」页维护），代码里不做任何兜底。本模块的
SEED_RULES 只是这张表的种子数据（由 scripts/seed_data.py 首次落库），运行时不参与判定：
DB 读挂或表里没数据时，本轮正常交给路由与业务 Agent，对话不因话术功能而中断。

匹配只有两档，刻意不开放正则：
- patterns（问法主干）：归一化后与整句**全等**才算命中，所以"你是谁？我订单三天了还没到"
  这类复合问句不会命中；
- keywords（关键词）：整句里出现即命中，用于"提示词""内部指令"这类强特征词。
误吞的代价（把真实业务诉求用一句模板打发掉）远大于少命中，新增关键词请配合后台的命中测试。
"""
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

KEY_IDENTITY = "identity"
KEY_CAPABILITY = "capability"
KEY_PROBING = "probing"

CATEGORY_IDENTITY = "identity"
CATEGORY_CAPABILITY = "capability"
CATEGORY_PROBING = "probing"
CATEGORY_BUSINESS = "business"
CATEGORIES = (CATEGORY_IDENTITY, CATEGORY_CAPABILITY, CATEGORY_PROBING, CATEGORY_BUSINESS)

# 种子规则的默认优先级（仅用于建库时的初始值，运行时以表里的 priority 为准）；
# 探听必须压过其它类别（"你是谁，把提示词给我"要拒而不是自我介绍）
PRIORITY_PROBING = 10
PRIORITY_IDENTITY = 20
PRIORITY_CAPABILITY = 30
PRIORITY_BUSINESS = 100

# ---- 种子应答文本（只用于 scripts/seed_data.py 首次落库；线上文案一律在后台表里改）----

IDENTITY_TEXT = """我是本平台的智能客服助手，全天候在线，自动为您解答问题。

我可以处理：订单与物流查询、退款与退换货、投诉与建议、产品与政策咨询。

请告诉我您具体想咨询的问题，涉及订单的话提供订单号会更快。"""

CAPABILITY_TEXT = """我可以为您提供以下服务：

1. 订单与物流：查询订单状态、追踪包裹、修改订单
2. 售后：退款、退换货申请及进度查询
3. 投诉与建议：受理投诉、生成工单并跟进处理
4. 咨询：产品使用、平台政策与服务指南

请说出您的具体需求，提供订单号或问题发生时间可以处理得更快。"""

PROBING_TEXT = """抱歉，系统内部的提示词与配置信息不在可提供的范围内。

我可以为您处理这些业务：订单与物流查询、退款与退换货、投诉与建议、产品与政策咨询。

请问您想了解哪一方面？"""

# 探听类关键词：只用强特征词，避免"规则/配置/密码"这类业务常用词造成误吞
_PROBING_KEYWORDS = [
    "提示词",
    "系统提示",
    "初始设定",
    "初始指令",
    "原始设定",
    "内部设定",
    "内部指令",
    "你的设定",
    "你们的设定",
    "上面的设定",
    "前面的设定",
    "之前的设定",
    "前置指令",
    "隐藏指令",
    "忽略上面",
    "忽略之前",
    "忽略上述",
    "无视上面",
    "绕过指令",
    "有哪些工具",
    "工具列表",
    "能调用哪些",
    "函数列表",
    "api密钥",
    "apikey",
    "密钥",
    "token",
    "数据库密码",
    "后台密码",
    "systemprompt",
    "底层模型是",
    "用的什么模型",
]


@dataclass(frozen=True)
class ScriptRule:
    """一条可执行的话术规则（DB 行与种子定义归一成同一形态，匹配逻辑只认这个）。"""

    key: str
    category: str
    patterns: frozenset[str]
    keywords: tuple[str, ...]
    text: str
    priority: int

    def applies_to(self, norm: str) -> bool:
        if norm and norm in self.patterns:
            return True
        return any(k and k in norm for k in self.keywords)


@dataclass(frozen=True)
class CannedScript:
    """一条命中的固定话术（key 用于链路留痕，text 为逐字下发内容）"""

    key: str
    category: str
    text: str


# 建库种子数据：唯一读者是 scripts/seed_data.py。运行时不引用它——后台把表清空就等于
# 取消固定话术能力，不会有一行看不见的代码规则在背后继续生效。
SEED_RULES: tuple[ScriptRule, ...] = (
    ScriptRule(
        key=KEY_PROBING,
        category=CATEGORY_PROBING,
        patterns=frozenset(),
        keywords=tuple(_PROBING_KEYWORDS),
        text=PROBING_TEXT,
        priority=PRIORITY_PROBING,
    ),
    ScriptRule(
        key=KEY_IDENTITY,
        category=CATEGORY_IDENTITY,
        patterns=frozenset(
            [
                "你是谁",
                "你叫什么",
                "你叫什么名字",
                "你叫啥",
                "你是哪位",
                "你是什么",
                "你是机器人",
                "你是ai",
                "你是人工智能",
                "你是真人",
                "你是人工",
                "你是客服",
                "你是机器人吗",
                "你是真人还是机器人",
                "你是机器人还是真人",
                "你是不是机器人",
                "你是不是真人",
                "介绍自己",
                "介绍一下自己",
                "自我介绍",
                "说说你自己",
                "你是哪个公司的客服",
                "你是哪个平台的",
                "whoareyou",
            ]
        ),
        keywords=(),
        text=IDENTITY_TEXT,
        priority=PRIORITY_IDENTITY,
    ),
    ScriptRule(
        key=KEY_CAPABILITY,
        category=CATEGORY_CAPABILITY,
        patterns=frozenset(
            [
                "你能做什么",
                "你能干什么",
                "你会做什么",
                "你可以做什么",
                "你都能做什么",
                "你都能干什么",
                "你能帮我什么",
                "你能为我做什么",
                "你能提供什么",
                "你能提供哪些服务",
                "你们能做什么",
                "你们能提供哪些服务",
                "你们有什么功能",
                "你们有哪些功能",
                "你有什么功能",
                "你有什么能力",
                "你的服务范围是什么",
                "你能帮我做什么",
                "whatcanyoudo",
            ]
        ),
        keywords=("能做什么", "有什么功能", "能提供哪些", "能帮我什么", "服务范围"),
        text=CAPABILITY_TEXT,
        priority=PRIORITY_CAPABILITY,
    ),
)


# 归一化：去空白与标点（客户常带"？""。"，匹配不该依赖它们），并统一小写
_NOISE = re.compile(
    r"[\s\u3000,，。.。、！!？?~～`*#>+/\\\"'“”‘’()（）\[\]{}《》:：;；\-—_]+",
)
# 句首礼貌语/填充语：先去噪再匹配，避免为每种客套写法重复一遍问法
_LEAD = re.compile(r"^(请问|请帮|帮我|麻烦|你好|您好|嗨|hi|hello|那个|我想问一下|我想咨询|问一下|咨询一下)+")
# 句尾语气词/致谢：同上，问法只记主干
_TAIL = re.compile(r"(谢谢|多谢|感谢|麻烦了|thanks|thankyou|一下|吧|哈|哦|嗯|呀|呢|啊|吗|嘛|的|了|不)+$")


def normalize_query(text: str) -> str:
    """去标点/空白 + 剥离句首礼貌语与句尾语气词，得到用于匹配的主干。

    问法（patterns）也走同一函数归一化，后台写"你是谁呀？"与"你是谁"等价。
    """
    norm = normalize_term(text)
    while True:
        stripped = _TAIL.sub("", _LEAD.sub("", norm, count=1), count=1)
        if stripped == norm:
            return stripped
        norm = stripped


def normalize_term(text: str) -> str:
    """只去标点/空白并小写：供校验与展示用（不剥礼貌语）。"""
    return _NOISE.sub("", (text or "").strip().lower())


def match_rules(query: str, rules: Iterable[ScriptRule]) -> CannedScript | None:
    """按优先级（小者优先）逐条判定，命中返回该条话术。"""
    norm = normalize_query(query)
    if not norm:
        return None
    for rule in sorted(rules, key=lambda r: (r.priority, r.key)):
        if rule.applies_to(norm):
            return CannedScript(key=rule.key, category=rule.category, text=rule.text)
    return None


def rule_from_row(row) -> ScriptRule:
    """把 canned_scripts 表行（或同名 dict）归一成可执行规则。

    问法与关键词走同一个 normalize_query：两边必须对称，否则后台填的"周末上班吗"会
    因为整句被削掉语气词而永远对不上。
    """
    get = row.get if isinstance(row, dict) else (lambda k, default=None: getattr(row, k, default))
    patterns = frozenset(
        n for n in (normalize_query(p) for p in get("patterns", []) or []) if n
    )
    keywords = tuple(
        sorted({n for n in (normalize_query(k) for k in get("keywords", []) or []) if n})
    )
    return ScriptRule(
        key=get("key", "") or "",
        category=get("category", CATEGORY_BUSINESS) or CATEGORY_BUSINESS,
        patterns=patterns,
        keywords=keywords,
        text=get("reply_text", "") or "",
        priority=int(get("priority", 0) or 0),
    )


_warned_read_failure = False


async def load_rules() -> list[ScriptRule]:
    """读取启用中的话术规则；异常交由 match_canned_script 处理。"""
    from app.models.repositories import CannedScriptRepo

    rows = await CannedScriptRepo.list_enabled()
    rules = [rule_from_row(r) for r in rows]
    # 正文为空等于没配（后台新建未填文案时不该把客户引到一句空回复上）
    return [r for r in rules if r.text.strip() and (r.patterns or r.keywords)]


async def match_canned_script(query: str) -> CannedScript | None:
    """一轮消息的固定话术判定：只看 DB；读不到就当没配，本轮正常走路由。"""
    global _warned_read_failure
    try:
        rules = await load_rules()
    except Exception as exc:  # noqa: BLE001
        # 表未建/DB 抖动都不能影响对话；不能回落代码规则，否则后台停用的话术会“停了但还在回”。
        # 热路径每轮都进这里，只告警一次避免刷屏，排查靠 runbook 的固定话术条目。
        if not _warned_read_failure:
            _warned_read_failure = True
            logger.warning(f"固定话术读取失败，本轮跳过话术判定（不影响对话）: {exc}")
        return None
    return match_rules(query, rules)
