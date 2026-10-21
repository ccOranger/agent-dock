"""HTTP 请求辅助：客户端 IP 解析（代理场景）。"""
import ipaddress

from fastapi import Request

from app.core.config import settings


def is_trusted_proxy(peer: str) -> bool:
    """直连对端是否在受信代理白名单内（支持单个 IP 与 CIDR）。"""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for item in settings.trusted_proxy_list:
        try:
            if "/" in item:
                if peer_ip in ipaddress.ip_network(item, strict=False):
                    return True
            elif peer_ip == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """取客户端 IP：仅当直连对端是受信代理时才采信 X-Forwarded-For 最左侧地址。

    无条件信任 XFF 会被伪造绕过限流；完全不看 XFF 时反向代理/NAT 后所有用户
    共享同一出口，错误密码 20 次就会锁死全站登录。
    """
    peer = request.client.host if request.client else "unknown"
    if is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip() or peer
    return peer
