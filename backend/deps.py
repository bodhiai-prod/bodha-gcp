"""Shared FastAPI app, router, DB, and small helpers.

Importing this module is side-effect free w.r.t. routes; modules that
register endpoints simply do `from deps import api_router` and decorate.
"""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from fastapi import APIRouter, FastAPI, HTTPException  # noqa: E402
from firestore_compat import FirestoreClient, FirestoreDatabase  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("bodhiai")

gcp_project_id = os.environ["GCP_PROJECT_ID"]
client = FirestoreClient()
db = FirestoreDatabase(gcp_project_id, os.environ.get("FIRESTORE_DATABASE", "(default)"))

app = FastAPI(title="Bodha AI - AI Question Paper Generator")
api_router = APIRouter(prefix="/api")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
