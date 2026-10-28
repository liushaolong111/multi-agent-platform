"""JWT 认证：登录签发 token + FastAPI 依赖注入解析当前用户。

AUTH_ENABLED=false（默认）时所有接口保持旧的免登录行为，
本地开发与 CI 测试不受影响；=true 时 /chat、/chat/stream 必须带
Authorization: Bearer <token>，用户身份从 token 解析而非请求体信任。
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from app.conf import config
from app.repository.users import authenticate

logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


def issue_token(user: dict) -> str:
    """签发 JWT，payload 携带用户身份与角色。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["user_id"],
        "role": user.get("role", "viewer"),
        "region": user.get("region", ""),
        "iat": now,
        "exp": now + timedelta(minutes=config.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解析 JWT 为用户上下文 dict；非法/过期抛 HTTPException(401)。"""
    try:
        payload = jwt.decode(
            token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM]
        )
        return {
            "user_id": payload["sub"],
            "role": payload.get("role", "viewer"),
            "region": payload.get("region", ""),
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的身份令牌")


# 未登录时的匿名身份（本地开发用）。用同一对象做哨兵，调用方可据此判断
# "本次请求是否真的带了 token"。
ANONYMOUS_USER = {"user_id": "default_user", "role": "admin", "region": ""}


async def get_current_user(authorization: str = Header(default="")) -> dict:
    """FastAPI 依赖：解析当前用户。

    - 带合法 Bearer token：一律以 token 身份为准（即使 AUTH_ENABLED=false）。
      这样 UI 登录区域经理账号后，数据权限立刻生效，无需为演示改服务端开关。
    - 不带 token：AUTH_ENABLED=false 回退匿名管理员（本地开发）；
      =true 抛 401（此时 token 是强制的）。
    """
    if authorization.startswith("Bearer "):
        return decode_token(authorization[len("Bearer "):])
    if not config.AUTH_ENABLED:
        return ANONYMOUS_USER
    raise HTTPException(status_code=401, detail="缺失 Authorization: Bearer 头")


def login(body: LoginRequest) -> LoginResponse:
    """登录接口处理：校验密码，签发 token。"""
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = issue_token(user)
    logger.info("[Auth] 用户登录成功：%s（%s）", user["user_id"], user["role"])
    return LoginResponse(
        access_token=token,
        expires_in=config.JWT_EXPIRE_MINUTES * 60,
        user=user,
    )


__all__ = [
    "ANONYMOUS_USER",
    "LoginRequest",
    "LoginResponse",
    "login",
    "get_current_user",
    "issue_token",
    "decode_token",
    "Depends",
]
