"""Firebase Authentication verification and Bodha role resolution."""
import os

import firebase_admin
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import exceptions as firebase_exceptions

from deps import db

security = HTTPBearer(auto_error=False)


def _firebase_app():
    """Initialise once, using Application Default Credentials on GCP."""
    if not firebase_admin._apps:  # Firebase Admin SDK has no public app registry.
        firebase_admin.initialize_app(options={"projectId": os.environ["GCP_PROJECT_ID"]})
    return firebase_admin.get_app()


def decode_token(token: str) -> dict:
    """Return verified Firebase claims, normalized for existing API callers."""
    try:
        claims = firebase_auth.verify_id_token(token, app=_firebase_app(), check_revoked=True)
    except (ValueError, firebase_exceptions.FirebaseError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired authentication token") from exc
    if not claims.get("uid") or not claims.get("email"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication token is missing identity details")
    return {**claims, "sub": claims["uid"]}


async def get_firebase_identity(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    claims = decode_token(creds.credentials)
    return {"id": claims["sub"], "email": str(claims["email"]).lower(), "claims": claims}


async def get_current_user(identity: dict = Depends(get_firebase_identity)) -> dict:
    """Map a verified Firebase identity to the application-owned user record."""
    user = await db.users.find_one({"id": identity["id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account setup is incomplete")
    return {"id": user["id"], "role": user.get("role", "teacher"), "email": user["email"]}


async def get_token_user(token: str) -> dict:
    """Resolve a Firebase bearer token for endpoints that also allow query tokens."""
    claims = decode_token(token)
    user = await db.users.find_one({"id": claims["sub"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account setup is incomplete")
    return {"id": user["id"], "role": user.get("role", "teacher"), "email": user["email"]}


def require_role(*roles: str):
    async def _dep(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _dep
