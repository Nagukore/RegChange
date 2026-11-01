"""Authentication and role-based authorisation.

Reviewer and admin operations (approving impact mappings, approving test cases,
starting executions) are gated here. Read-only endpoints stay open in
development so the dashboard works before anyone signs in; set
ENVIRONMENT=production to require a token everywhere.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.enums import UserRole
from app.models.ops import User

# auto_error=False so optional-auth dependencies can fall through cleanly.
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """bcrypt used directly; passlib adds a layer we do not need here."""
    # bcrypt silently truncates beyond 72 bytes, so reject rather than mislead
    # the caller into thinking a longer password was fully used.
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be at most 72 bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        # Malformed stored hash or oversized input -- treat as a failed login
        # rather than surfacing a 500.
        return False


def create_access_token(*, subject: str, role: str, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired"
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc


def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    db: Session = Depends(get_db),
) -> User | None:
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    email = payload.get("sub")
    if not email:
        return None
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)] = None,
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    return user


# Ordered most to least privileged; a role satisfies any requirement at or
# below its own level.
_ROLE_RANK = {
    UserRole.ADMIN.value: 4,
    UserRole.REVIEWER.value: 3,
    UserRole.ANALYST.value: 2,
    UserRole.VIEWER.value: 1,
}


class RequireRole:
    """Dependency factory enforcing a minimum role.

    In development, when no user has been created yet, this permits the request
    and the audit record is attributed to "unauthenticated-dev". That shortcut
    is disabled whenever ENVIRONMENT is production or any user exists.
    """

    def __init__(self, minimum: UserRole) -> None:
        self.minimum = minimum

    def __call__(
        self,
        user: Annotated[User | None, Depends(get_current_user_optional)] = None,
        db: Session = Depends(get_db),
    ) -> User | None:
        if user is not None:
            if _ROLE_RANK.get(user.role, 0) < _ROLE_RANK[self.minimum.value]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires role {self.minimum.value} or higher",
                )
            return user

        if settings.environment.lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        any_user_exists = db.execute(select(User.id).limit(1)).scalar_one_or_none() is not None
        if any_user_exists:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None


require_reviewer = RequireRole(UserRole.REVIEWER)
require_analyst = RequireRole(UserRole.ANALYST)
require_admin = RequireRole(UserRole.ADMIN)


def actor_label(user: User | None) -> str:
    """Stable string written into audit and approval records."""
    return user.email if user else "unauthenticated-dev"
