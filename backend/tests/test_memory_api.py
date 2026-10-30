"""记忆查询接口：访客主体透传、type/limit 透传、非法客户 ID 拒绝。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import memory as mem_api
from app.models.repositories import MemoryRepo

USER_ID = "377dfa00-48fe-48f9-8f58-c89a43ce6fe8"
VISITOR_ID = "c82623b6-35c7-41c4-b8e7-5b88b5153277"


@pytest.fixture
def client(make_test_app):
    app = make_test_app([mem_api.router], prefix="")
    return TestClient(make_test_app.as_role(app, "agent"))


@pytest.fixture
def captured(monkeypatch):
    """把仓储层换成记录参数的桩，避免连库。"""
    box: dict = {"rows": []}

    async def fake_list(subject, type_=None, limit=200):
        box["subject"] = subject
        box["type"] = type_
        box["limit"] = limit
        return box["rows"]

    monkeypatch.setattr(MemoryRepo, "list_by_user", fake_list)
    return box


def test_bare_uuid_passthrough(client, captured):
    resp = client.get(f"/memory/users/{USER_ID}")
    assert resp.status_code == 200
    assert captured["subject"] == USER_ID
    assert captured["limit"] == 200


def test_visitor_prefix_passthrough(client, captured):
    """嵌入渠道的匿名访客只有 visitor_id，必须原样把 visitor: 前缀交给仓储层。"""
    resp = client.get(f"/memory/users/visitor:{VISITOR_ID}")
    assert resp.status_code == 200
    assert captured["subject"] == f"visitor:{VISITOR_ID}"


def test_type_and_limit_passthrough(client, captured):
    resp = client.get(f"/memory/users/{USER_ID}", params={"type": "episodic", "limit": 20})
    assert resp.status_code == 200
    assert (captured["type"], captured["limit"]) == ("episodic", 20)


@pytest.mark.parametrize(
    "bad",
    ["not-a-uuid", "visitor:oops", "123", "visitor:"],
)
def test_invalid_subject_is_rejected_with_400(client, captured, bad):
    """非法串若直传仓储层会抛 ValueError 变 500，接口层必须先挡成 400。"""
    resp = client.get(f"/memory/users/{bad}")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001
    assert "subject" not in captured  # 没有打到仓储层


def test_rows_are_serialized(client, captured):
    row = type(
        "Row",
        (),
        {
            "id": uuid.UUID(USER_ID),
            "memory_uid": f"{USER_ID}:fb873b50392f54a6",
            "type": "episodic",
            "content": "我要投诉你们客服",
            "importance": 0.44,
            "access_count": 2,
            "created_at": __import__("datetime").datetime(2026, 10, 27, 1, 29, 9),
        },
    )()
    captured["rows"] = [row]

    resp = client.get(f"/memory/users/{USER_ID}")
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["memory_uid"] == row.memory_uid
    assert data[0]["content"] == "我要投诉你们客服"
    assert data[0]["access_count"] == 2
