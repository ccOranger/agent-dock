"""常量定义"""
from enum import StrEnum

API_PREFIX = "/api/v1"


class UserRole(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    ADMIN = "admin"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    HANDOVER = "handover"


class MemoryType(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PERCEPTUAL = "perceptual"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class ToolType(StrEnum):
    MCP = "mcp"
    CUSTOM = "custom"
    BUILTIN = "builtin"


DEFAULT_MEMORY_TYPES = ["working", "episodic", "semantic", "perceptual"]
