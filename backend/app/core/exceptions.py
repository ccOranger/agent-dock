"""业务异常与错误码（对应 Spec 第 13 章）"""
from fastapi import HTTPException, status


class BizError(HTTPException):
    code: int = 5001
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str | None = None, data: dict | None = None):
        super().__init__(
            status_code=self.http_status,
            detail={"code": self.code, "message": message or self.__doc__, "data": data},
        )


class ParamError(BizError):
    code = 1001
    http_status = status.HTTP_400_BAD_REQUEST
    __doc__ = "参数校验失败"


class Unauthorized(BizError):
    code = 2001
    http_status = status.HTTP_401_UNAUTHORIZED
    __doc__ = "未认证"


class TokenExpired(BizError):
    code = 2002
    http_status = status.HTTP_401_UNAUTHORIZED
    __doc__ = "Token 过期"


class AccountNotRegistered(BizError):
    code = 2004
    http_status = status.HTTP_404_NOT_FOUND
    __doc__ = "账号不存在，请先注册"


class Forbidden(BizError):
    code = 2003
    http_status = status.HTTP_403_FORBIDDEN
    __doc__ = "无权限"


class NotFound(BizError):
    code = 3001
    http_status = status.HTTP_404_NOT_FOUND
    __doc__ = "资源不存在"


class Conflict(BizError):
    code = 3002
    http_status = status.HTTP_409_CONFLICT
    __doc__ = "资源冲突"


class SceneNotFound(BizError):
    code = 3003
    http_status = status.HTTP_404_NOT_FOUND
    __doc__ = "场景不存在"


class SceneDisabled(BizError):
    code = 3004
    http_status = status.HTTP_404_NOT_FOUND
    __doc__ = "场景已停用"


class SceneNotConfigured(BizError):
    code = 3005
    http_status = status.HTTP_409_CONFLICT
    __doc__ = "场景未配置可用 Agent"


class ToolParamError(BizError):
    code = 4001
    http_status = status.HTTP_400_BAD_REQUEST
    __doc__ = "工具参数校验失败"


class UnsafeInput(BizError):
    code = 4003
    http_status = status.HTTP_400_BAD_REQUEST
    __doc__ = "输入包含不安全内容"


class RateLimited(BizError):
    code = 4029
    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    __doc__ = "请求过于频繁"


class InternalError(BizError):
    code = 5001
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    __doc__ = "内部错误"


class LLMUnavailable(BizError):
    code = 5002
    http_status = status.HTTP_502_BAD_GATEWAY
    __doc__ = "LLM 服务不可用"


class RagSearchFailed(BizError):
    code = 5003
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    __doc__ = "RAGTool 检索失败"


class ToolTimeout(BizError):
    code = 5004
    http_status = status.HTTP_504_GATEWAY_TIMEOUT
    __doc__ = "工具调用超时"


class MemoryToolFailed(BizError):
    code = 5005
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    __doc__ = "MemoryTool 调用失败"


class AgentNotRegistered(BizError):
    code = 5006
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    __doc__ = "Agent 未注册"
