"""
Authentication dependencies — JWT creation/verification, role guards.
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


def _get_secret_key() -> str:
    if not settings.jwt_secret_key:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Add it to your HuggingFace secrets."
        )
    return settings.jwt_secret_key


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, username: str, role: str, full_access: bool) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "full_access": full_access,
        "exp": expire,
    }
    return jwt.encode(payload, _get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _get_secret_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    user_id = int(payload["sub"])
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_write_access(user: User = Depends(get_current_user)) -> User:
    """Allows admin or viewer with full_access."""
    if user.role == "admin":
        return user
    if user.full_access:
        return user
    raise HTTPException(status_code=403, detail="Write access required")


def seed_admin_user(db: Session) -> None:
    """Create admin user from env vars if it doesn't exist."""
    if not settings.admin_password:
        logger.warning("ADMIN_PASSWORD not set — skipping admin seed")
        return
    existing = db.scalar(
        select(User).where(User.username == settings.admin_username)
    )
    if existing:
        # Update password hash in case it changed
        new_hash = hash_password(settings.admin_password)
        if not verify_password(settings.admin_password, existing.password_hash):
            existing.password_hash = new_hash
            existing.role = "admin"
            existing.is_active = True
            db.commit()
            logger.info(f"Admin user '{settings.admin_username}' password updated.")
        return
    user = User(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        role="admin",
        full_access=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    logger.info(f"Admin user '{settings.admin_username}' created.")
