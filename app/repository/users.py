"""用户仓储：密码哈希、登录认证与用户创建。

密码用 hashlib.pbkdf2_hmac（stdlib，零新依赖；生产可换 bcrypt/argon2）。
"""
import hashlib
import hmac
import logging
import os

from app.model.base import Base
from app.model.user import VALID_ROLES, User

logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 100_000


def hash_password(password: str, salt: str | None = None) -> str:
    """PBKDF2-HMAC-SHA256 哈希，格式：salt$hex_digest。"""
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较，防时序侧信道。"""
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    ).hex()
    return hmac.compare_digest(candidate, digest)


def authenticate(username: str, password: str) -> dict | None:
    """校验用户名密码，成功返回用户上下文 dict，失败返回 None。"""
    from app.repository.business import business_engine, get_business_session

    if not business_engine:
        logger.warning("[Auth] 业务库不可用，无法认证")
        return None
    # 确保 users 表存在
    Base.metadata.create_all(business_engine)

    session = get_business_session()
    try:
        user = session.query(User).filter_by(username=username).first()
        if user and verify_password(password, user.password_hash):
            return {
                "user_id": user.username,
                "role": user.role,
                "region": user.region or "",
            }
        return None
    except Exception as e:
        logger.warning("[Auth] 认证查询失败：%s", e)
        return None
    finally:
        session.close()


def create_user(username: str, password: str, role: str, region: str = "") -> bool:
    """创建/覆盖用户（种子脚本用）。"""
    from app.repository.business import business_engine, get_business_session

    if role not in VALID_ROLES:
        raise ValueError(f"非法角色：{role}，可选：{VALID_ROLES}")
    if not business_engine:
        return False
    Base.metadata.create_all(business_engine)

    session = get_business_session()
    try:
        existing = session.query(User).filter_by(username=username).first()
        if existing:
            existing.password_hash = hash_password(password)
            existing.role = role
            existing.region = region
        else:
            session.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    region=region,
                )
            )
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.warning("[Auth] 创建用户失败：%s", e)
        return False
    finally:
        session.close()
