"""JWT auth utilities."""
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
JWT_EXP_DAYS = 7

security = HTTPBearer()

# Dedicated pool for password hashing.
#
# bcrypt is deliberately CPU-expensive (~215ms at cost factor 12), so it must not
# run on the event loop. But it must not share the *default* executor either:
# that pool is dominated by Bedrock calls which park a thread for 30-120s, so a
# login could sit behind one and take half a minute (measured: 34.6s max under
# load). A separate pool keeps auth latency independent of LLM traffic.
#
# Kept small on purpose. Unlike the LLM calls this is CPU-bound work, so beyond
# roughly cpu_count the threads just contend for cores without adding throughput
# — the win here is isolation, not size.
AUTH_POOL_MAX_WORKERS = int(
    os.environ.get("AUTH_POOL_MAX_WORKERS", os.cpu_count() or 4)
)
_AUTH_POOL = ThreadPoolExecutor(
    max_workers=AUTH_POOL_MAX_WORKERS, thread_name_prefix="auth"
)


def shutdown_auth_pool() -> None:
    """Release the auth pool's threads on application shutdown."""
    _AUTH_POOL.shutdown(wait=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


async def hash_password_async(plain: str) -> str:
    """hash_password() on the dedicated auth pool. Use from async endpoints."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_AUTH_POOL, hash_password, plain)


async def verify_password_async(plain: str, hashed: str) -> bool:
    """verify_password() on the dedicated auth pool. Use from async endpoints."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_AUTH_POOL, verify_password, plain, hashed)


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
    creds: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    payload = decode_token(creds.credentials)
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
