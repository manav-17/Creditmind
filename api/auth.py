"""
CreditMind - Authentication for the API.

  * POST /auth/login with the officer password returns a signed token (12 hours)
  * The React app sends it as:  Authorization: Bearer <token>
  * Scripts can still use:      X-API-Key: <API_KEY>
  * If neither DASHBOARD_PASSWORD nor API_KEY is set (local development), the API is open

Tokens are HMAC-SHA256 signed with AUTH_SECRET, so they cannot be forged or edited.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from fastapi import Header, HTTPException

TOKEN_HOURS = 12


def _secret():
    secret = os.getenv("AUTH_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="AUTH_SECRET is not configured")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(subject: str = "officer") -> dict:
    expires = int(time.time()) + TOKEN_HOURS * 3600
    payload = _b64(json.dumps({"sub": subject, "exp": expires}).encode())
    signature = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return {"token": f"{payload}.{signature}", "expires_at": expires}


def verify_token(token: str) -> Optional[dict]:
    try:
        payload, signature = token.split(".")
        expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        return data if data.get("exp", 0) > time.time() else None
    except (ValueError, json.JSONDecodeError):
        return None


def check_password(password: str) -> bool:
    expected = os.getenv("DASHBOARD_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(password, expected)


def auth_enabled() -> bool:
    return bool(os.getenv("DASHBOARD_PASSWORD") or os.getenv("API_KEY"))


def require_auth(authorization: Optional[str] = Header(None),
                 x_api_key: Optional[str] = Header(None)):
    """FastAPI dependency: accepts a Bearer token or the X-API-Key header."""
    if not auth_enabled():
        return {"sub": "local-dev"}
    api_key = os.getenv("API_KEY")
    if api_key and x_api_key and hmac.compare_digest(x_api_key, api_key):
        return {"sub": "api-key"}
    if authorization and authorization.lower().startswith("bearer "):
        data = verify_token(authorization[7:].strip())
        if data:
            return data
    raise HTTPException(status_code=401, detail="Not authenticated",
                        headers={"WWW-Authenticate": "Bearer"})