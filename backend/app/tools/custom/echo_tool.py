"""自定义工具：echo（连通性自检）。

不涉及任何具体业务，也不访问外部依赖：把入参原样回显。
用于验证「模型发起 [TOOL_CALL:...] → 系统解析执行 → 结果回填 → 模型续答」
整条工具调用链路是否打通，冒烟测试与排障时最省事。
"""
from app.tools.custom.base import BaseTool


class EchoTool(BaseTool):
    name = "echo"
    description = (
        "原样回显传入的文本，用于验证工具调用链路是否连通（不涉及任何业务数据）。"
        "调用格式 [TOOL_CALL:echo:text=任意文本]；"
        "参数：text=要回显的文本(必填)。返回收到的同一段文本。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要原样回显的文本"},
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    timeout = 5

    async def run(self, text: str = ""):
        return {"ok": True, "data": {"text": text}}
