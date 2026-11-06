"""Flexible JWT Authentication & Dev-Mode Bypass Handler.

Implements production-grade HS256 Bearer JWT verification alongside a flexible
development-mode bypass (Decision Q5) allowing direct header overrides or
tokenless testing when DEV_MODE=True.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException, Request, status
import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)


def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    secret: Optional[str] = None,
    algorithm: Optional[str] = None,
) -> str:
    """Generate a signed HS256 JWT access token."""
    settings = get_settings()
    sec = secret or settings.jwt_secret
    alg = algorithm or settings.jwt_algorithm

    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(hours=2)

    to_encode.update({"exp": expire})
    if "iat" not in to_encode:
        to_encode["iat"] = now

    encoded_jwt = jwt.encode(to_encode, sec, algorithm=alg)
    return encoded_jwt


def verify_token(
    token: str,
    secret: Optional[str] = None,
    algorithm: Optional[str] = None,
) -> dict[str, Any]:
    """Verify and decode a signed HS256 JWT access token."""
    settings = get_settings()
    sec = secret or settings.jwt_secret
    alg = algorithm or settings.jwt_algorithm

    try:
        payload = jwt.decode(token, sec, algorithms=[alg])
        return payload
    except jwt.ExpiredSignatureError as exc:
        logger.warning("Token verification failed: token expired: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token has expired: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("Token verification failed: invalid token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authorization token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def is_dev_mode() -> bool:
    """Return whether development mode bypass is enabled."""
    return get_settings().dev_mode


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
    x_role: Optional[str] = Header(default=None, alias="X-Role"),
) -> dict[str, Any]:
    """FastAPI dependency resolving the authenticated user context.

    Enforces Bearer JWT verification in production. In development mode
    (DEV_MODE=True), permits tokenless requests with fallback to headers
    or default credentials, and bypasses mismatched host ERP tokens.
    """
    dev_mode = is_dev_mode()

    if authorization is not None:
        parts = authorization.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            if not dev_mode:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authorization header format. Expected 'Bearer <token>'.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        else:
            token = parts[1]
            try:
                payload = verify_token(token)
                return payload
            except HTTPException:
                if not dev_mode:
                    raise
                logger.info("DEV_MODE active: bypassing mismatched host ERP token.")

    # Missing Authorization header
    if not dev_mode:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # In DEV_MODE: allow missing token and honor header overrides
    tenant_id = x_tenant_id or "shree_shashwatraj_agro"
    user_id = x_user_id or "admin_1"
    role = x_role or "admin"
    return {
        "sub": user_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "is_dev": True,
    }


async def get_tenant_id(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> str:
    """FastAPI dependency extracting the active tenant ID from user context."""
    return current_user.get("tenant_id") or "default"
