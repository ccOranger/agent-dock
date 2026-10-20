"""安全模块：JWT 签发/校验、密码哈希、输入输出安全"""
import re
import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ===== Prompt Injection 检测模式 =====
INJECTION_PATTERNS = [
    r"ignore\s+(previous|above)\s+instructions",
    r"system\s*prompt",
    r"you\s+are\s+now\s+",
    r"disregard\s+all\s+prior",
]
_COMPILED_INJECTION = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# ===== 输出脱敏模式 =====
SENSITIVE_PATTERNS = [
    (re.compile(r"\b1[3-9]\d{9}\b"), "1**********"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "******************"),
    (re.compile(r"\b\d{16,19}\b"), "**** **** **** ****"),
]


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(sub: str, role: str, ttl: int, token_type: str = "access") -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": str(uuid.uuid4()),
        "typ": token_type,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, role: str) -> str:
    return create_token(user_id, role, settings.access_token_ttl, "access")


def create_refresh_token(user_id: str, role: str) -> str:
    return create_token(user_id, role, settings.refresh_token_ttl, "refresh")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def detect_injection(text: str) -> bool:
    """检测 Prompt Injection 攻击，命中返回 True"""
    return any(p.search(text) for p in _COMPILED_INJECTION)


def sanitize_output(text: str) -> str:
    """输出脱敏：手机号 / 身份证 / 银行卡"""
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
