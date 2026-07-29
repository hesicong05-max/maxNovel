"""Authentication utilities — password hashing, JWT token creation/verification."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.models.user import User

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a password with bcrypt.

    bcrypt has a 72-byte limit on passwords. We truncate to 72 bytes
    to handle longer passwords gracefully.
    """
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    pwd_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))


def create_access_token(user_id: str, username: str) -> str:
    """Create a JWT access token with iat/jti claims."""
    import uuid as _uuid
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.JWT_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,  # Issued-at time
        "jti": _uuid.uuid4().hex,  # Unique token ID (for future revocation support)
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT token. Returns payload or raises."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 Token")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency: extract and verify the current user from the Bearer token.

    Returns None if no token is provided (for optional auth endpoints).
    Raises 401 if token is invalid.
    """
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 中缺少用户信息")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Dependency: like get_current_user but returns None instead of raising 401.

    Use this for endpoints that work both authenticated and unauthenticated
    (e.g., listing community novels — anyone can view, only authed users can manage).
    """
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return None

        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
    except HTTPException:
        return None


async def get_admin_user(
    user: User = Depends(get_current_user),
) -> User:
    """Dependency: require admin privileges.

    Raises 403 if the user is not an admin.
    """
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


async def get_project_for_owner(
    project_id: str,
    user: User,
    db: AsyncSession,
) -> Project:
    """Load a project and verify ownership.

    Raises:
        404 if project not found.
        403 if user is not the owner.

    Legacy projects without an owner are intentionally denied. Allowing every
    authenticated user to access them would expose private project data across
    accounts. Such rows must be assigned to an owner through an administrative
    migration before they become accessible.
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id is None or project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作此项目")
    return project
