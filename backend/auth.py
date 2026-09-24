"""Session, password, and Google OpenID Connect authentication utilities."""
import os
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
JWT_EXP_DAYS = 7

# ``auto_error=False`` lets browser sessions use the HttpOnly cookie while
# preserving bearer-token support for any existing API clients during rollout.
security = HTTPBearer(auto_error=False)
SESSION_COOKIE_NAME = "bodha_session"
PASSWORD_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """Create a salted PBKDF2-SHA256 password hash for local accounts."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS, salt.hex(), digest.hex()
    )


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Safely verify a password hash, including malformed legacy values."""
    if not stored_hash:
        return False
    try:
        algorithm, iterations, salt_hex, expected_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        ).hex()
        return secrets.compare_digest(actual, expected_hex)
    except (TypeError, ValueError):
        return False

def create_token(user_id: str, role: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXP_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    token = creds.credentials if creds else request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = decode_token(token)
    return {
        "id": payload["sub"],
        "role": payload.get("role", "teacher"),
        "email": payload.get("email"),
    }


def require_role(*roles: str):
    async def _dep(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _dep
