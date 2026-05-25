"""
FastAPI 依赖注入集中处。

- bearer_scheme:      HTTPBearer 安全方案,负责让 Swagger UI 自动出现 Authorize 按钮
- get_current_user:   解 JWT -> 返回当前 User 实例,鉴权失败抛 401
- require_admin_dep:  在 get_current_user 之上检查角色为 admin,否则 403

说明:之前用 `Header(default="")` 接收 Authorization 头,Swagger UI 不知道这是个
安全方案,所以右上角没有 Authorize 按钮,每次 Try it out 都要在那个 header 输入框
里手填 "Bearer xxx",容易出错。换成 HTTPBearer 后:
  1) Swagger UI 右上角会出现绿色 Authorize 按钮
  2) 点开后只需粘贴 token (不带 Bearer 前缀)
  3) 之后所有接口自动带上 Authorization: Bearer <token>
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token
from app.models.user import Role, User

# auto_error=False 让我们自己决定怎么报错(不带 token 时给个明确的中文提示),
# 而不是 FastAPI 默认的 "Not authenticated" 英文消息。
bearer_scheme = HTTPBearer(auto_error=False, description="粘贴 /auth/login 返回的 access_token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """
    从 Authorization: Bearer <token> 中解 JWT,再去库里查出对应 User。
    任一环节失败统一抛 401(账号被禁用是 403)。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 Bearer Token")
    token = credentials.credentials

    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token 无效或已过期")

    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token 内容异常")

    user = await User.filter(id=user_id).first()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    return user


async def require_admin_dep(user: User = Depends(get_current_user)) -> User:
    """
    管理员鉴权 —— 在 path operation 上 Depends(require_admin_dep) 即可。
    内部先走 get_current_user 拿到 User,再校验角色。
    """
    if user.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要 admin 权限")
    return user
