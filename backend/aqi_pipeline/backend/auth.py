"""Authentication helpers for backend API."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

_bearer = HTTPBearer(auto_error=False)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """
    Require API key auth using either:
    - `Authorization: Bearer <key>`
    - `X-API-Key: <key>`
    """
    settings = get_settings()
    if not settings.auth_required:
        return "auth-disabled"

    if not settings.api_keys:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "auth_misconfigured",
                "message": "AQI_API_KEYS is not configured on server",
            },
        )

    provided = None
    if x_api_key:
        provided = x_api_key.strip()
    elif credentials is not None and credentials.scheme.lower() == "bearer":
        provided = credentials.credentials.strip()

    if not provided or provided not in settings.api_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "unauthorized",
                "message": "Missing or invalid API key",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    return provided

