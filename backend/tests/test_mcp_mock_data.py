"""固定数据集 mock_data 的行为契约测试（纯逻辑，无网络/子进程）"""
import pytest

from app.packs.customer_service.tools import mock_data as md


@pytest.fixture(autouse=True)
def _isolate_state():
    """每个用例前后快照/还原可变状态（订单可被修改、退款单会被追加）。"""
    orders = {k: dict(v) for k, v in md._ORDERS.items()}
    refunds = {k: dict(v) for k, v in md._REFUNDS.items()}
    seq = md._refund_seq
    yield
    md._ORDERS.clear()
    md._ORDERS.update(orders)
    md._REFUNDS.clear()
    md._REFUNDS.update(refunds)
    md._refund_seq = seq


def test_get_order_known_and_unknown():
    order = md.get_order("SO20240001")
    assert order is not None
    assert order["customer_id"] == "alice"
    assert order["status"] == "shipped"
    assert md.get_order("NOT_EXIST") is None


def test_get_order_returns_copy_not_reference():
    order = md.get_order("SO20240001")
    order["status"] = "hacked"
    assert md.get_order("SO20240001")["status"] == "shipped"


def test_list_orders_by_customer_sorted_desc():
    orders = md.list_orders("alice")
    assert orders
    assert all(o["customer_id"] == "alice" for o in orders)
    created = [o["created_at"] for o in orders]
    assert created == sorted(created, reverse=True)


def test_list_orders_unknown_customer_empty():
    assert md.list_orders("nobody") == []


def test_update_order_only_editable_fields():
    updated, err = md.update_order("SO20240002", {"remark": "改备注", "status": "cancelled"})
    assert err is None
    assert updated["remark"] == "改备注"
    # status 不在白名单，未被修改
    assert updated["status"] == "paid"


def test_update_order_unknown_id():
    updated, err = md.update_order("NOPE", {"remark": "x"})
    assert updated is None and err == "订单不存在"


def test_update_order_rejects_non_editable_only():
    updated, err = md.update_order("SO20240001", {"amount": 1})
    assert updated is None and err and "没有可修改" in err


def test_logistics_only_for_shipped_or_delivered():
    assert md.get_logistics("SO20240001") is not None      # shipped
    assert md.get_logistics("SO20240003") is not None      # delivered
    assert md.get_logistics("SO20240002") is None          # paid 未发货


def test_every_logistics_order_exists_and_consistent():
    for oid, rec in md._LOGISTICS.items():
        order = md.get_order(oid)
        assert order is not None, f"物流记录 {oid} 缺少对应订单"
        assert order["status"] in {"shipped", "delivered"}
        assert rec["order_id"] == oid
        assert isinstance(rec["traces"], list) and rec["traces"]


def test_submit_refund_success_creates_pending():
    refund, err = md.submit_refund("SO20240004", "商品与描述不符")
    assert err is None
    assert refund["status"] == "pending"
    assert refund["order_id"] == "SO20240004"
    assert refund["refund_id"].startswith("RF")
    # 落库后可按订单查回
    assert md.get_refund_by_order("SO20240004")["refund_id"] == refund["refund_id"]


def test_submit_refund_rejects_cancelled_order():
    refund, err = md.submit_refund("SO20240005", "不想要了")
    assert refund is None and err and "已取消" in err


def test_submit_refund_rejects_unknown_order():
    refund, err = md.submit_refund("NOPE", "x")
    assert refund is None and err == "订单不存在，无法申请退款"


def test_submit_refund_rejects_duplicate():
    _, err1 = md.submit_refund("SO20240002", "第一次")
    assert err1 is None
    refund, err2 = md.submit_refund("SO20240002", "重复提交")
    assert refund is None and err2 and "已有退款申请" in err2


def test_existing_refund_queryable():
    rec = md.get_refund("RF20240001")
    assert rec is not None and rec["status"] == "refunded"
    assert md.get_refund_by_order("SO20240005")["refund_id"] == "RF20240001"
