"""Object storage backed by Google Cloud Storage.

The public surface intentionally matches the former S3 adapter so persisted
``storage_path`` values and all callers remain unchanged.

Configuration:
    GCP_PROJECT_ID    Google Cloud project (optional when ADC supplies it)
    STORAGE_BUCKET   destination bucket (required)
    STORAGE_PREFIX   object prefix inside the bucket (default ``bodhiai``)
    APP_NAME         caller namespace (default ``qpgen``)
"""
from __future__ import annotations

import logging
import os
import threading

from google.api_core.retry import Retry
from google.cloud import storage as gcs

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID") or None
BUCKET = os.environ.get("STORAGE_BUCKET", "")
PREFIX = os.environ.get("STORAGE_PREFIX", "bodhiai").strip("/")
APP_NAME = os.environ.get("APP_NAME", "qpgen")
LOAD_TEST_STUB = os.environ.get("LOAD_TEST_STUB", "").lower() in ("1", "true", "yes")

_STUB_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

_retry = Retry(initial=1.0, maximum=10.0, multiplier=2.0, deadline=120.0)
_client_lock = threading.Lock()
_client: gcs.Client | None = None
_bucket: gcs.Bucket | None = None


def _key(path: str) -> str:
    path = path.lstrip("/")
    return f"{PREFIX}/{path}" if PREFIX else path


def _ensure_client() -> tuple[gcs.Client, gcs.Bucket]:
    global _client, _bucket
    if _client is None or _bucket is None:
        with _client_lock:
            if _client is None or _bucket is None:
                if not BUCKET:
                    raise RuntimeError(
                        "STORAGE_BUCKET is not set — object storage is unavailable"
                    )
                client = gcs.Client(project=GCP_PROJECT_ID)
                bucket = client.bucket(BUCKET)
                bucket.reload(retry=_retry, timeout=30)
                _client, _bucket = client, bucket
                logger.info("Storage initialized (gs://%s/%s)", BUCKET, PREFIX)
    return _client, _bucket


def init_storage() -> str:
    """Initialize ADC-backed storage and verify the bucket is reachable."""
    if LOAD_TEST_STUB:
        return BUCKET or "stub-bucket"
    _ensure_client()
    return BUCKET


def check_storage() -> bool:
    """Perform a live metadata read for readiness checks."""
    if LOAD_TEST_STUB:
        return True
    _, bucket = _ensure_client()
    bucket.reload(retry=_retry, timeout=10)
    return True


def put_object(path: str, data: bytes, content_type: str) -> dict:
    if LOAD_TEST_STUB:
        return {"path": path, "key": _key(path), "bucket": BUCKET or "stub-bucket"}
    _, bucket = _ensure_client()
    key = _key(path)
    bucket.blob(key).upload_from_string(
        data,
        content_type=content_type,
        retry=_retry,
        timeout=120,
    )
    return {"path": path, "key": key, "bucket": BUCKET}


def get_object(path: str) -> tuple[bytes, str]:
    if LOAD_TEST_STUB:
        return _STUB_PDF, "application/pdf"
    _, bucket = _ensure_client()
    blob = bucket.blob(_key(path))
    data = blob.download_as_bytes(retry=_retry, timeout=120)
    if not blob.content_type:
        blob.reload(retry=_retry, timeout=30)
    return data, blob.content_type or "application/octet-stream"
