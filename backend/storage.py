"""Object storage backed by AWS S3.

Replaces the original Emergent object-store client. The public surface is
unchanged — `init_storage`, `put_object`, `get_object`, and `APP_NAME` keep the
same signatures and return shapes, so callers in server.py / workers.py /
competitive_routes.py did not change.

Object keys are `{STORAGE_PREFIX}/{path}`, where `path` is what callers pass in
(and what gets persisted to Mongo as `storage_path`). Changing STORAGE_PREFIX
after objects exist will orphan them — pick one and leave it alone.

Configuration (via environment / backend/.env):
    AWS_REGION       bucket region
    STORAGE_BUCKET   destination bucket (required)
    STORAGE_PREFIX   key prefix inside the bucket (default "bodhiai")
    APP_NAME         path namespace used by callers (default "qpgen")
"""
import logging
import os
import threading

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BUCKET = os.environ.get("STORAGE_BUCKET", "")
PREFIX = os.environ.get("STORAGE_PREFIX", "bodhiai").strip("/")
APP_NAME = os.environ.get("APP_NAME", "qpgen")

# See llm_adapter.LOAD_TEST_STUB. Object storage is stubbed too because from a
# developer machine an S3 round-trip measures home upstream bandwidth, not the
# server — in production EC2 -> S3 in-region is far faster, so leaving S3 live
# during a local load test overstates upload cost and distorts the result.
LOAD_TEST_STUB = os.environ.get("LOAD_TEST_STUB", "").lower() in ("1", "true", "yes")

# Minimal one-page PDF, returned by get_object() under the stub so callers that
# re-parse a stored file (e.g. reindex) still receive something pypdf can open.
_STUB_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

_client_lock = threading.Lock()
_s3 = None


def _key(path: str) -> str:
    path = path.lstrip("/")
    return f"{PREFIX}/{path}" if PREFIX else path


def init_storage() -> str:
    """Create (and cache) the S3 client and verify the bucket is reachable.

    Called once at app startup so credential / bucket problems surface in the
    boot log rather than on the first upload. Returns the bucket name.
    """
    global _s3
    if LOAD_TEST_STUB:
        # No credentials or bucket required while stubbed.
        return BUCKET or "stub-bucket"
    if _s3 is None:
        with _client_lock:
            if _s3 is None:
                if not BUCKET:
                    raise RuntimeError(
                        "STORAGE_BUCKET is not set — object storage is unavailable"
                    )
                client = boto3.client(
                    "s3",
                    region_name=AWS_REGION,
                    config=Config(
                        retries={"max_attempts": 3, "mode": "standard"},
                        read_timeout=120,
                        connect_timeout=10,
                    ),
                )
                client.head_bucket(Bucket=BUCKET)
                _s3 = client
                logger.info(f"Storage initialized (s3://{BUCKET}/{PREFIX})")
    return BUCKET


def put_object(path: str, data: bytes, content_type: str) -> dict:
    if LOAD_TEST_STUB:
        return {"path": path, "key": _key(path), "bucket": BUCKET or "stub-bucket"}
    init_storage()
    key = _key(path)
    _s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type)
    return {"path": path, "key": key, "bucket": BUCKET}


def get_object(path: str) -> tuple[bytes, str]:
    if LOAD_TEST_STUB:
        return (_STUB_PDF, "application/pdf")
    init_storage()
    resp = _s3.get_object(Bucket=BUCKET, Key=_key(path))
    return (
        resp["Body"].read(),
        resp.get("ContentType", "application/octet-stream"),
    )
