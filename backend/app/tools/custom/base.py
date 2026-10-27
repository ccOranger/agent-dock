"""项目内兼容基类 BaseTool

公开版 hello-agents 0.2.x 只提供同步的 `Tool`（构造需传 name/description、
抽象方法 get_parameters()->List[ToolParameter]、同步 run(dict)）。

本项目的自定义工具采用「类属性 + async run(**kwargs)」写法（见 conftest 桩与
orchestrator 的异步执行链），故在此对 `Tool` 做一层薄适配：
- __init__：从子类的 name/description 类属性读取并初始化
- get_parameters：把子类的 parameters(JSON-schema dict) 转成 ToolParameter 列表
子类只需按原有写法声明 name/description/parameters 并实现 async run 即可。
"""
from typing import Any

from hello_agents.tools import Tool, ToolParameter


class BaseTool(Tool):
    # 子类通过类属性覆盖
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def __init__(self) -> None:
        super().__init__(name=self.name, description=self.description)

    def get_parameters(self) -> list[ToolParameter]:
        props: dict[str, Any] = self.parameters.get("properties", {}) or {}
        required = set(self.parameters.get("required", []) or [])
        result: list[ToolParameter] = []
        for pname, pdef in props.items():
            pdef = pdef or {}
            result.append(
                ToolParameter(
                    name=pname,
                    type=pdef.get("type", "string"),
                    description=pdef.get("description", ""),
                    required=pname in required,
                )
            )
        return result

    def run(self, parameters: dict[str, Any]) -> Any:  # noqa: D401
        """占位实现；子类以 async run(**kwargs) 覆盖。"""
        raise NotImplementedError
