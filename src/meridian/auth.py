"""Single-user access control: one secret key, sent as `Authorization: Bearer <key>`.

Fails closed: outside the dev environment, a missing or weak key blocks every
protected endpoint instead of leaving the API open.
"""

import hmac

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from meridian.config import Settings, get_settings

MIN_TOKEN_LENGTH = 16

bearer = HTTPBearer(auto_error=False, description="Your MERIDIAN_API_TOKEN")


def parse_origins(value: str) -> list[str]:
    return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]


def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    token = settings.api_token
    if not token:
        if settings.env == "dev":
            return  # local development: open, so the browser address bar still works
        raise HTTPException(
            status_code=503,
            detail="MERIDIAN_API_TOKEN is not set; the API is locked outside dev.",
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise HTTPException(
            status_code=503,
            detail=f"MERIDIAN_API_TOKEN is too short (minimum {MIN_TOKEN_LENGTH} characters).",
        )
    supplied = creds.credentials if creds and creds.scheme.lower() == "bearer" else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid access key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
