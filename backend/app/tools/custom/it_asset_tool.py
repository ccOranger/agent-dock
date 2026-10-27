"""自定义工具：IT 资产查询。"""
from app.tools.custom import mock_assets
from app.tools.custom.base import BaseTool


class QueryItAssetTool(BaseTool):
    name = "query_it_asset"
    description = (
        "查询 IT 资产信息（型号、序列号、领用人、状态、保修期、IP/MAC）。查询资产时必须先调用。"
        "调用格式 [TOOL_CALL:query_it_asset:keyword=资产编号]；"
        "参数：keyword=资产编号/序列号(SN)/领用人/型号/IP/MAC 任一；"
        "查电脑类设备可直接用“电脑”。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "资产编号、序列号、领用人、型号、部门、位置、IP 或 MAC",
            },
        },
        "required": ["keyword"],
        "additionalProperties": False,
    }
    timeout = 10

    async def run(self, keyword: str):
        assets = mock_assets.find_assets(keyword)
        if not assets:
            return {"ok": False, "error": "未查询到匹配资产，请核对资产编号或序列号"}
        return {"ok": True, "data": {"count": len(assets), "assets": assets}}
