"""固定 IT 资产数据集——进程内资产查询工具使用

设计要点：
- 纯标准库、只读快照：进程重启即回到初始数据，满足演示/测试对固定数据的诉求。
- 覆盖笔记本、台式机、显示器、打印机、服务器等常见资产类型，以及「在用/库存」状态，
  便于演示按资产编号、序列号、领用人、IP/MAC 等多种方式查询。

可用示例资产编号：
    IT-LT-1001 张伟的 Dell 笔记本（在用）      IT-LT-1002 李娜的 ThinkPad（在用）
    IT-DT-2001 王强的 HP 台式机（在用）        IT-MON-3001 张伟的 Dell 显示器
    IT-PRN-4001 3F 打印区公共打印机            IT-SRV-9001 机房华为服务器
"""
from __future__ import annotations

from copy import deepcopy

# asset_no -> 资产记录
_ASSETS: dict[str, dict] = {
    "IT-LT-1001": {
        "asset_no": "IT-LT-1001",
        "type": "笔记本",
        "brand": "Dell",
        "model": "Latitude 5440",
        "sn": "5CD4K3X7Q2",
        "owner": "张伟",
        "department": "财务部",
        "status": "in_use",
        "status_text": "在用",
        "location": "北京总部 5F",
        "warranty_end": "2027-03-15",
        "ip": "10.20.31.45",
        "mac": "3C:52:82:1A:4B:10",
    },
    "IT-LT-1002": {
        "asset_no": "IT-LT-1002",
        "type": "笔记本",
        "brand": "Lenovo",
        "model": "ThinkPad T14 Gen 4",
        "sn": "PF3Y2K8M",
        "owner": "李娜",
        "department": "市场部",
        "status": "in_use",
        "status_text": "在用",
        "location": "上海分部 3F",
        "warranty_end": "2026-11-30",
        "ip": "10.30.12.88",
        "mac": "A4:BB:6D:2C:91:07",
    },
    "IT-DT-2001": {
        "asset_no": "IT-DT-2001",
        "type": "台式机",
        "brand": "HP",
        "model": "EliteDesk 800 G9",
        "sn": "CZC4123ABC",
        "owner": "王强",
        "department": "研发部",
        "status": "in_use",
        "status_text": "在用",
        "location": "北京总部 4F",
        "warranty_end": "2028-06-20",
        "ip": "10.20.44.12",
        "mac": "8C:16:45:33:AA:21",
    },
    "IT-MON-3001": {
        "asset_no": "IT-MON-3001",
        "type": "显示器",
        "brand": "Dell",
        "model": "U2723QE",
        "sn": "CN0M3K2P",
        "owner": "张伟",
        "department": "财务部",
        "status": "in_use",
        "status_text": "在用",
        "location": "北京总部 5F",
        "warranty_end": "2027-03-15",
        "ip": "",
        "mac": "",
    },
    "IT-PRN-4001": {
        "asset_no": "IT-PRN-4001",
        "type": "打印机",
        "brand": "HP",
        "model": "LaserJet Pro M404dn",
        "sn": "VNB3J12345",
        "owner": "公共设备",
        "department": "行政部",
        "status": "in_use",
        "status_text": "在用",
        "location": "北京总部 3F 打印区",
        "warranty_end": "2026-09-30",
        "ip": "10.20.50.31",
        "mac": "00:1E:0B:4F:22:9A",
    },
    "IT-SRV-9001": {
        "asset_no": "IT-SRV-9001",
        "type": "服务器",
        "brand": "华为",
        "model": "FusionServer 2288H V6",
        "sn": "2102312TQK10M5000123",
        "owner": "IT 运维组",
        "department": "信息技术部",
        "status": "in_use",
        "status_text": "在用",
        "location": "北京机房 A2",
        "warranty_end": "2029-01-10",
        "ip": "10.20.0.10",
        "mac": "6C:92:BF:10:88:01",
    },
}

# 参与关键字匹配的字段（不含状态等枚举，避免“在用”这类词误命中全部）
_SEARCHABLE_FIELDS = ("asset_no", "type", "brand", "model", "sn", "owner", "department", "location", "ip", "mac")

# 品类别名：用户口语里的“电脑”应覆盖笔记本与台式机
_TYPE_ALIASES: dict[str, set[str]] = {
    "电脑": {"笔记本", "台式机"},
    "pc": {"笔记本", "台式机"},
    "笔记本": {"笔记本"},
    "台式机": {"台式机"},
    "显示器": {"显示器"},
    "打印机": {"打印机"},
    "服务器": {"服务器"},
}


def find_assets(keyword: str, *, limit: int = 10) -> list[dict]:
    """按关键字返回匹配的资产副本；无匹配返回空列表。

    支持资产编号、序列号（SN）、领用人、型号、部门、位置、IP、MAC，
    以及“电脑/笔记本/台式机/显示器/打印机/服务器”等品类别名。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return []

    aliases = _TYPE_ALIASES.get(kw)
    if aliases:
        hits = [a for a in _ASSETS.values() if a["type"] in aliases]
    else:
        hits = [
            a
            for a in _ASSETS.values()
            if any(kw in str(a.get(field, "")).lower() for field in _SEARCHABLE_FIELDS)
        ]
    return [deepcopy(a) for a in hits[:limit]]
