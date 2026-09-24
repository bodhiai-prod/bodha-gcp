#!/usr/bin/env python3
"""Preflight the GCP production dependencies.

This creates and removes one GCS object, one Firestore document, one tiny
Vision OCR request, and one local file. The Vision request is billable after
the account's monthly free allowance. It does not make an LLM generation call.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import requests
from google.auth import default
from google.cloud import firestore, storage, vision
from PIL import Image, ImageDraw


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def check_adc() -> None:
    credentials, project = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(__import__("google.auth.transport.requests", fromlist=["Request"]).Request())
    print(f"OK ADC project={project or os.environ.get('GCP_PROJECT_ID', '<unset>')}")


def check_storage() -> None:
    bucket_name = require("STORAGE_BUCKET")
    prefix = os.environ.get("STORAGE_PREFIX", "bodhiai").strip("/")
    key = f"{prefix}/preflight/{uuid.uuid4().hex}.txt" if prefix else f"preflight/{uuid.uuid4().hex}.txt"
    bucket = storage.Client(project=os.environ.get("GCP_PROJECT_ID") or None).bucket(bucket_name)
    bucket.reload(timeout=20)
    blob = bucket.blob(key)
    blob.upload_from_string(b"bodha-gcp-preflight", content_type="text/plain", timeout=30)
    assert blob.download_as_bytes(timeout=30) == b"bodha-gcp-preflight"
    blob.delete(timeout=30)
    print(f"OK GCS gs://{bucket_name}/{prefix}")


def check_vision() -> None:
    image = Image.new("RGB", (500, 120), "white")
    ImageDraw.Draw(image).text((20, 40), "BODHA GCP OCR 123", fill="black")
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        image.save(tmp.name)
        content = Path(tmp.name).read_bytes()
    response = vision.ImageAnnotatorClient().document_text_detection(
        image=vision.Image(content=content), timeout=30
    )
    if response.error.message:
        raise RuntimeError(response.error.message)
    text = response.full_text_annotation.text.strip()
    if "BODHA" not in text.upper():
        raise RuntimeError(f"Vision returned unexpected OCR: {text!r}")
    print(f"OK Vision OCR {text!r}")


def check_firestore() -> None:
    client = firestore.Client(
        project=require("GCP_PROJECT_ID"),
        database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
    )
    reference = client.collection("_gcp_preflight").document(uuid.uuid4().hex)
    try:
        reference.set({"probe": True})
        assert reference.get().to_dict() == {"probe": True}
    finally:
        reference.delete()
    print("OK Firestore read/write/delete")


def check_openrouter() -> None:
    response = requests.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {require('OPENROUTER_API_KEY')}"},
        timeout=20,
    )
    response.raise_for_status()
    print("OK OpenRouter API authentication")


def check_log_volume() -> None:
    log_dir = Path(os.environ.get("TOKEN_USAGE_CSV", "/srv/bodha/logs/llm_usage.csv")).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    probe = log_dir / f".preflight-{uuid.uuid4().hex}"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    print(f"OK persistent log path {log_dir}")


def main() -> int:
    checks = (check_adc, check_storage, check_vision, check_firestore, check_openrouter, check_log_volume)
    failures = 0
    for check in checks:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {check.__name__}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
