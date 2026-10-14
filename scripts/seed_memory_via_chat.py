"""通过模拟正常聊天对话生成记忆管理测试数据。

前置条件：
  1. 后端已启动（默认 http://localhost:8000）
  2. 已执行 seed_data.py 初始化 alice 用户

用法（在项目根目录或 backend 目录下）：
    python scripts/seed_memory_via_chat.py
    python scripts/seed_memory_via_chat.py --base-url http://localhost:8000 --username alice --password Alice@123
"""
import argparse
import sys
import time

import httpx

# ─── 模拟对话场景 ───────────────────────────────────────────────
# 每个场景是一组多轮对话（仅 user 消息，assistant 由后端回复）
# 设计覆盖：订单查询、物流追踪、投诉、退款、知识问答 等意图以触发不同路由 Agent

CONVERSATIONS = [
    # 场景1：订单查询 + 含订单号（触发 semantic 记忆 + order_id metadata）
    {
        "title": "查询订单物流",
        "messages": [
            "你好，我想查一下我的订单 ORD20241001 到哪里了",
            "这个订单是我10月1号买的，是一个蓝牙耳机",
            "大概什么时候能到？我手机号是 13812345678，方便快递员联系",
        ],
    },
    # 场景2：投诉（高情感强度 + 含订单号 → 触发 semantic 记忆）
    {
        "title": "产品投诉",
        "messages": [
            "我要投诉！你们发的东西太差了，订单号 2024088801",
            "收到的是坏的，完全是骗人的行为，我要退款",
            "我已经等了三天了没人处理，太差劲了，再不处理我就举报",
            "给我转人工，我对这个机器人回复非常不满意",
        ],
    },
    # 场景3：退换货咨询（多轮对话，turn_count 逐渐增高）
    {
        "title": "退换货申请",
        "messages": [
            "我想退货，订单 TN-20240315 的东西尺码不合适",
            "退货流程是什么样的？需要寄回还是上门取",
            "退款一般多久到账",
            "我的手机号 15987654321，能安排上门取件吗",
        ],
    },
    # 场景4：知识问答（通用FAQ，多轮累积 turn_count → 触发 episodic 记忆）
    {
        "title": "产品使用问题",
        "messages": [
            "你们的智能手表支持哪些手机系统",
            "防水等级是多少，可以游泳戴吗",
            "怎么连接蓝牙耳机，我试了好几次都连不上",
            "电池续航怎么样，一天要充几次",
            "有没有儿童模式，怎么设置",
            "我手机是华为mate60，适配吗",
            "表带可以自己换吗，哪里买配件",
            "保修期多长，需要发票吗",
        ],
    },
    # 场景5：包含工单号的售后跟进（触发 ticket_id metadata）
    {
        "title": "售后工单跟进",
        "messages": [
            "我之前提了个工单 TK-5678，想问一下处理进度",
            "已经过了5个工作日了还没有回复",
            "我的联系电话是 18600001234，请加急处理",
        ],
    },
    # 场景6：重复提及订单号（触发 repeat_mention 信号）
    {
        "title": "重复催单",
        "messages": [
            "我的订单 ORD20240920 什么时候发货",
            "已经付了钱了，ORD20240920 到现在还没动静",
            "你们是不是忘了 ORD20240920 这个单了，我要退款！",
        ],
    },
]


def login(client: httpx.Client, base_url: str, username: str, password: str) -> str:
    """登录获取 access_token（人员/客户分表后走客户登录入口）。"""
    resp = client.post(
        f"{base_url}/api/v1/auth/customer/login",
        json={"username": username, "password": password},
    )
    if resp.status_code != 200:
        print(f"❌ 登录失败: {resp.status_code} {resp.text}")
        sys.exit(1)
    data = resp.json()
    token = data["data"]["access_token"]
    print(f"✅ 登录成功: {username}")
    return token


def send_chat(client: httpx.Client, base_url: str, token: str, session_id: str | None, message: str) -> dict:
    """发送聊天消息（非流式接口），返回响应 data。"""
    resp = client.post(
        f"{base_url}/api/v1/chat/message",
        headers={"Authorization": f"Bearer {token}"},
        json={"session_id": session_id, "message": message},
    )
    if resp.status_code != 200:
        print(f"  ⚠️  发送失败 [{resp.status_code}]: {resp.text[:200]}")
        return {}
    body = resp.json()
    return body.get("data", {})


def check_memories(client: httpx.Client, base_url: str, token: str) -> list:
    """查询当前用户的记忆列表。"""
    resp = client.get(
        f"{base_url}/api/v1/memory/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        print(f"  ⚠️  查询记忆失败: {resp.text[:200]}")
        return []
    body = resp.json()
    return body.get("data", [])


def main():
    parser = argparse.ArgumentParser(description="通过模拟聊天生成记忆数据")
    parser.add_argument("--base-url", default="http://localhost:8000", help="后端地址")
    parser.add_argument("--username", default="alice", help="登录用户名")
    parser.add_argument("--password", default="Alice@123", help="登录密码")
    parser.add_argument("--delay", type=float, default=1.0, help="每条消息间隔秒数")
    args = parser.parse_args()

    print(f"🚀 开始生成记忆数据 → {args.base_url} (用户: {args.username})")
    print("=" * 60)

    with httpx.Client(timeout=120) as client:
        token = login(client, args.base_url, args.username, args.password)

        for idx, conv in enumerate(CONVERSATIONS, 1):
            title = conv["title"]
            messages = conv["messages"]
            session_id = None  # 每个场景新建会话

            print(f"\n📋 场景 {idx}: {title} ({len(messages)} 轮)")
            print("-" * 40)

            for msg_idx, msg in enumerate(messages, 1):
                result = send_chat(client, args.base_url, token, session_id, msg)
                if result:
                    session_id = result.get("session_id", session_id)
                    routed = result.get("routed_to", "?")
                    reply_preview = (result.get("content") or "")[:60]
                    print(f"  [{msg_idx}] user: {msg[:50]}...")
                    print(f"      agent({routed}): {reply_preview}...")
                time.sleep(args.delay)

        # 最终验证
        print("\n" + "=" * 60)
        print("🔍 验证已生成的记忆数据：")
        memories = check_memories(client, args.base_url, token)
        if memories:
            print(f"  共 {len(memories)} 条记忆记录")
            for m in memories[:10]:  # 最多展示10条
                print(f"  • [{m['type']}] importance={m['importance']:.2f} | {m['content'][:50]}")
            if len(memories) > 10:
                print(f"  ... 还有 {len(memories) - 10} 条")
        else:
            print("  ⚠️  暂无记忆数据（可能后端未运行或 LLM 服务不可用）")

    print("\n✅ 完成！")


if __name__ == "__main__":
    main()
