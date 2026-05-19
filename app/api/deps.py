"""
FastAPI 依赖注入集中处。现在先放占位,后续添加:
  - get_current_user
  - require_admin
  - get_agv_manager
"""

from fastapi import Header, HTTPException, status

from app.core.security import decode_access_token


async def get_current_user_sub(authorization: str = Header(default="")) -> str:
    """
    最简实现：仅从 Authorization: Bearer <token> 里拍 payload 的 sub。
    后续接入 User 表后这里返回 User 实例。
    """
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return payload["sub"]
