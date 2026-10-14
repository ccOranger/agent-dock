"""固定业务数据集（订单 / 物流 / 退款）——进程内工具与本地 MCP 服务共用

设计要点：
- 纯标准库、无外部依赖：既能在 FastAPI 进程内被自定义工具 import，也能被
  以 stdio 子进程方式启动的 MCP server（backend/mcp_servers/*.py）import。
- 只读快照 + 少量可写动作（改订单备注 / 提交退款），退款单在内存里累加，
  进程重启即回到初始数据，满足"提供一批固定的数据"的演示/测试诉求。

可用示例订单号（覆盖不同状态）：
    SO20240001 已发货（有物流）      SO20240002 已支付（待发货）
    SO20240003 已签收（有物流）      SO20240004 处理中
    SO20240005 已取消                SO20240006 已发货（有物流）
"""
from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime

# ============================ 订单 ============================

# order_id -> 订单记录
_ORDERS: dict[str, dict] = {
    "SO20240001": {
        "order_id": "SO20240001",
        "customer_id": "alice",
        "status": "shipped",
        "status_text": "已发货",
        "created_at": "2024-05-01 10:22:00",
        "amount": 299.00,
        "currency": "CNY",
        "address": "北京市海淀区中关村大街 1 号",
        "remark": "",
        "items": [
            {"sku": "SKU-A100", "name": "无线蓝牙耳机", "qty": 1, "price": 199.00},
            {"sku": "SKU-B200", "name": "耳机收纳盒", "qty": 1, "price": 100.00},
        ],
    },
    "SO20240002": {
        "order_id": "SO20240002",
        "customer_id": "alice",
        "status": "paid",
        "status_text": "已支付，待发货",
        "created_at": "2024-05-03 14:05:00",
        "amount": 1288.00,
        "currency": "CNY",
        "address": "北京市海淀区中关村大街 1 号",
        "remark": "希望工作日送达",
        "items": [
            {"sku": "SKU-C300", "name": "智能手表", "qty": 1, "price": 1288.00},
        ],
    },
    "SO20240003": {
        "order_id": "SO20240003",
        "customer_id": "u_1002",
        "status": "delivered",
        "status_text": "已签收",
        "created_at": "2024-04-20 09:10:00",
        "amount": 459.00,
        "currency": "CNY",
        "address": "上海市浦东新区世纪大道 100 号",
        "remark": "",
        "items": [
            {"sku": "SKU-D400", "name": "机械键盘", "qty": 1, "price": 459.00},
        ],
    },
    "SO20240004": {
        "order_id": "SO20240004",
        "customer_id": "alice",
        "status": "processing",
        "status_text": "处理中",
        "created_at": "2024-05-05 20:31:00",
        "amount": 59.90,
        "currency": "CNY",
        "address": "北京市海淀区中关村大街 1 号",
        "remark": "",
        "items": [
            {"sku": "SKU-E500", "name": "手机壳", "qty": 2, "price": 29.95},
        ],
    },
    "SO20240005": {
        "order_id": "SO20240005",
        "customer_id": "u_1003",
        "status": "cancelled",
        "status_text": "已取消",
        "created_at": "2024-04-28 11:00:00",
        "amount": 1999.00,
        "currency": "CNY",
        "address": "广州市天河区天河路 208 号",
        "remark": "客户主动取消",
        "items": [
            {"sku": "SKU-F600", "name": "平板电脑", "qty": 1, "price": 1999.00},
        ],
    },
    "SO20240006": {
        "order_id": "SO20240006",
        "customer_id": "u_1002",
        "status": "shipped",
        "status_text": "已发货",
        "created_at": "2024-05-04 16:40:00",
        "amount": 758.00,
        "currency": "CNY",
        "address": "上海市浦东新区世纪大道 100 号",
        "remark": "",
        "items": [
            {"sku": "SKU-G700", "name": "便携充电宝", "qty": 2, "price": 379.00},
        ],
    },
}

# ============================ 物流 ============================

# order_id -> 物流轨迹（仅对已发货/已签收订单存在）
_LOGISTICS: dict[str, dict] = {
    "SO20240001": {
        "order_id": "SO20240001",
        "carrier": "顺丰速运",
        "tracking_no": "SF1234567890",
        "status": "in_transit",
        "status_text": "运输中",
        "eta": "2024-05-07",
        "traces": [
            {"time": "2024-05-05 08:12", "desc": "【北京】快件已从北京海淀集散中心发出"},
            {"time": "2024-05-04 19:30", "desc": "【北京】快件已到达北京海淀集散中心"},
            {"time": "2024-05-04 10:05", "desc": "【北京】顺丰已收取快件"},
        ],
    },
    "SO20240003": {
        "order_id": "SO20240003",
        "carrier": "中通快递",
        "tracking_no": "ZT8888666644",
        "status": "delivered",
        "status_text": "已签收",
        "eta": "2024-04-24",
        "traces": [
            {"time": "2024-04-24 15:20", "desc": "【上海】快件已由本人签收，感谢使用中通"},
            {"time": "2024-04-24 09:00", "desc": "【上海】派件中，快递员：王师傅 138****0001"},
            {"time": "2024-04-23 20:10", "desc": "【上海】快件已到达上海浦东分拨中心"},
        ],
    },
    "SO20240006": {
        "order_id": "SO20240006",
        "carrier": "京东物流",
        "tracking_no": "JD9999000011",
        "status": "in_transit",
        "status_text": "运输中",
        "eta": "2024-05-08",
        "traces": [
            {"time": "2024-05-06 07:40", "desc": "【上海】快件已到达上海松江分拣中心"},
            {"time": "2024-05-05 21:00", "desc": "【北京】快件已从北京亦庄仓发出"},
        ],
    },
}

# ============================ 退款 ============================

# refund_id -> 退款单
_REFUNDS: dict[str, dict] = {
    "RF20240001": {
        "refund_id": "RF20240001",
        "order_id": "SO20240005",
        "reason": "下单后主动取消",
        "status": "refunded",
        "status_text": "已退款",
        "amount": 1999.00,
        "created_at": "2024-04-28 12:00:00",
        "updated_at": "2024-04-29 09:00:00",
    },
}

_lock = threading.Lock()
_refund_seq = 2  # 已有 RF20240001


# ============================ 查询 / 动作 ============================


def get_order(order_id: str) -> dict | None:
    """按订单号返回订单副本；不存在返回 None。"""
    order = _ORDERS.get((order_id or "").strip())
    return deepcopy(order) if order else None


def list_orders(customer_id: str) -> list[dict]:
    """返回某客户的全部订单（按创建时间倒序）。"""
    cid = (customer_id or "").strip()
    orders = [deepcopy(o) for o in _ORDERS.values() if o["customer_id"] == cid]
    orders.sort(key=lambda o: o["created_at"], reverse=True)
    return orders


# 允许修改的字段白名单（避免任意字段写入）
_EDITABLE_FIELDS = {"address", "remark"}


def update_order(order_id: str, fields: dict) -> tuple[dict | None, str | None]:
    """修改订单字段。返回 (更新后的订单, 错误信息)。二者互斥。"""
    oid = (order_id or "").strip()
    order = _ORDERS.get(oid)
    if order is None:
        return None, "订单不存在"
    applied = {k: v for k, v in (fields or {}).items() if k in _EDITABLE_FIELDS}
    if not applied:
        return None, f"没有可修改的字段（仅支持：{', '.join(sorted(_EDITABLE_FIELDS))}）"
    with _lock:
        order.update(applied)
    return deepcopy(order), None


def get_logistics(order_id: str) -> dict | None:
    """按订单号返回物流轨迹副本；无物流记录返回 None。"""
    rec = _LOGISTICS.get((order_id or "").strip())
    return deepcopy(rec) if rec else None


def _next_refund_id() -> str:
    global _refund_seq
    with _lock:
        rid = f"RF2024{str(_refund_seq).zfill(4)}"
        _refund_seq += 1
    return rid


def submit_refund(order_id: str, reason: str) -> tuple[dict | None, str | None]:
    """提交退款申请（模拟受理，不承诺金额）。返回 (退款单, 错误信息)。"""
    oid = (order_id or "").strip()
    order = _ORDERS.get(oid)
    if order is None:
        return None, "订单不存在，无法申请退款"
    if order["status"] == "cancelled":
        return None, "该订单已取消，无需再申请退款"
    existing = get_refund_by_order(oid)
    if existing:
        return None, f"该订单已有退款申请（{existing['refund_id']}，{existing['status_text']}）"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    refund = {
        "refund_id": _next_refund_id(),
        "order_id": oid,
        "reason": (reason or "").strip() or "未填写原因",
        "status": "pending",
        "status_text": "已受理，待审核",
        "amount": order["amount"],
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        _REFUNDS[refund["refund_id"]] = refund
    return deepcopy(refund), None


def get_refund_by_order(order_id: str) -> dict | None:
    """按订单号查最新退款单。"""
    oid = (order_id or "").strip()
    recs = [r for r in _REFUNDS.values() if r["order_id"] == oid]
    if not recs:
        return None
    recs.sort(key=lambda r: r["created_at"], reverse=True)
    return deepcopy(recs[0])


def get_refund(refund_id: str) -> dict | None:
    """按退款单号查退款单。"""
    rec = _REFUNDS.get((refund_id or "").strip())
    return deepcopy(rec) if rec else None
