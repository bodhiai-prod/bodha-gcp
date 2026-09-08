"""Preflight check for the AWS side of the app.

Verifies, in order: credentials -> which Claude/image models this account can
actually use in this region -> a real Converse round-trip -> S3 put/get/delete.
Run it before starting the backend; almost every "the app doesn't work" failure
is one of these four, and they are indistinguishable from inside the app.

Diagrams may run in a region of their own (BEDROCK_IMAGE_REGION); when they do,
the image checks are aimed at that region rather than AWS_REGION.

    python scripts/check_bedrock.py
    python scripts/check_bedrock.py --image    # also bills one test image

Exit code is 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "backend" / ".env")

REGION = os.environ.get("AWS_REGION", "us-east-1")
# Diagrams may live in a different region from everything else — see
# BEDROCK_IMAGE_REGION in llm_adapter.py. Model access is granted per region,
# so the image check has to run against its own region to mean anything.
IMAGE_REGION = os.environ.get("BEDROCK_IMAGE_REGION", REGION)
MODEL_DEFAULT = os.environ.get("BEDROCK_MODEL_DEFAULT", "")
MODEL_FALLBACK = os.environ.get("BEDROCK_MODEL_FALLBACK", "")
IMAGE_MODEL = os.environ.get("BEDROCK_IMAGE_MODEL", "stability.sd3-5-large-v1:0")
BUCKET = os.environ.get("STORAGE_BUCKET", "")
PREFIX = os.environ.get("STORAGE_PREFIX", "bodhiai").strip("/")

OK, FAIL, INFO = "[ ok ]", "[FAIL]", "[ .. ]"
failures: list[str] = []


def fail(step: str, err: object) -> None:
    failures.append(step)
    print(f"{FAIL} {step}: {err}")


def check_identity() -> None:
    print(f"{INFO} region: {REGION}")
    if IMAGE_REGION != REGION:
        print(f"{INFO} image region: {IMAGE_REGION} (diagrams only)")
    try:
        ident = boto3.client("sts", region_name=REGION).get_caller_identity()
        print(f"{OK} credentials: {ident['Arn']}")
    except (ClientError, BotoCoreError) as e:
        fail("credentials", e)


def list_image_models() -> None:
    """List the text-to-image models available in the diagram region.

    Only runs when diagrams are in a region of their own. Model access is
    granted per region, so access in AWS_REGION says nothing about whether
    BEDROCK_IMAGE_MODEL is actually usable in BEDROCK_IMAGE_REGION — this is
    the check that catches a missing grant before the app silently drops every
    diagram (generate_diagram() swallows failures by design).
    """
    try:
        bedrock = boto3.client("bedrock", region_name=IMAGE_REGION)
        models = bedrock.list_foundation_models()["modelSummaries"]
    except (ClientError, BotoCoreError) as e:
        fail(f"list_foundation_models ({IMAGE_REGION})", e)
        return

    usable = sorted(
        m["modelId"]
        for m in models
        if "IMAGE" in m.get("outputModalities", [])
        and "ON_DEMAND" in m.get("inferenceTypesSupported", [])
    )
    print(f"{OK} on-demand image models in {IMAGE_REGION} ({len(usable)}):")
    for m in usable:
        print(f"        {m}")
    if IMAGE_MODEL not in usable:
        print(
            f"\n        BEDROCK_IMAGE_MODEL={IMAGE_MODEL} is NOT in that list.\n"
            f"        Request access in the Bedrock console for {IMAGE_REGION}\n"
            "        (Model access), or pick an id that does appear.\n"
        )


def list_models() -> None:
    """Show the Claude + image models this account can invoke here.

    Both on-demand model ids and inference-profile ids are listed — the
    us.anthropic.* ids in .env are inference profiles (cross-region routing),
    which is what most accounts need for current Claude models.
    """
    try:
        bedrock = boto3.client("bedrock", region_name=REGION)
    except (ClientError, BotoCoreError) as e:
        fail("bedrock client", e)
        return

    try:
        models = bedrock.list_foundation_models()["modelSummaries"]
        usable = [
            m["modelId"]
            for m in models
            if ("anthropic." in m["modelId"] or "IMAGE" in m.get("outputModalities", []))
            and "ON_DEMAND" in m.get("inferenceTypesSupported", [])
        ]
        print(f"{OK} on-demand foundation models ({len(usable)}):")
        for m in sorted(usable):
            print(f"        {m}")
    except (ClientError, BotoCoreError) as e:
        fail("list_foundation_models", e)

    try:
        profiles = bedrock.list_inference_profiles()["inferenceProfileSummaries"]
        claude = [
            p["inferenceProfileId"]
            for p in profiles
            if "anthropic" in p["inferenceProfileId"]
        ]
        print(f"{OK} inference profiles ({len(claude)} Claude):")
        for p in sorted(claude):
            print(f"        {p}")
    except (ClientError, BotoCoreError) as e:
        # Not fatal — some regions/accounts don't expose this API.
        print(f"{INFO} list_inference_profiles unavailable: {e}")

    print(
        "\n        Whatever you set as BEDROCK_MODEL_DEFAULT / _FALLBACK must appear\n"
        "        in one of the two lists above. If it doesn't, request access in\n"
        "        the Bedrock console (Model access) or pick an id that does.\n"
    )


def check_converse(model_id: str, label: str) -> None:
    if not model_id:
        fail(f"converse ({label})", "model id not set in backend/.env")
        return
    try:
        rt = boto3.client("bedrock-runtime", region_name=REGION)
        resp = rt.converse(
            modelId=model_id,
            system=[{"text": "You are a terse assistant."}],
            messages=[{"role": "user", "content": [{"text": "Reply with exactly: pong"}]}],
            inferenceConfig={"maxTokens": 16, "temperature": 0},
        )
        text = "".join(
            b["text"] for b in resp["output"]["message"]["content"] if "text" in b
        ).strip()
        usage = resp.get("usage", {})
        print(
            f"{OK} converse ({label}) {model_id} -> {text!r} "
            f"[in {usage.get('inputTokens')} / out {usage.get('outputTokens')} tokens]"
        )
    except (ClientError, BotoCoreError) as e:
        fail(f"converse ({label}) {model_id}", e)


def check_image() -> None:
    """Bill one real image against the diagram region.

    Mirrors llm_adapter._generate_image_sync: same Stability schema, same
    region variable. If this passes and diagrams still don't appear, the
    problem is in the app, not in AWS.
    """
    try:
        rt = boto3.client("bedrock-runtime", region_name=IMAGE_REGION)
        body = {
            "prompt": "a simple black and white line drawing of a triangle",
            "mode": "text-to-image",
            "aspect_ratio": "1:1",
            "output_format": "png",
        }
        resp = rt.invoke_model(
            modelId=IMAGE_MODEL,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        n = len(payload.get("images") or [])
        reasons = payload.get("finish_reasons") or []
        note = f" [filtered: {reasons[0]}]" if reasons and reasons[0] else ""
        print(
            f"{OK} image model {IMAGE_MODEL} in {IMAGE_REGION} -> "
            f"{n} image(s) returned{note}"
        )
    except (ClientError, BotoCoreError) as e:
        fail(f"image model {IMAGE_MODEL} ({IMAGE_REGION})", e)


def check_textract() -> None:
    """Render a tiny image with known text and OCR it.

    Catches a missing textract:DetectDocumentText IAM permission here, where
    it's one clear line, rather than mid-upload where it looks like an
    unexplained empty extraction.
    """
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (400, 100), "white")
        ImageDraw.Draw(img).text((10, 40), "BODHI OCR TEST 123", fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        client = boto3.client("textract", region_name=REGION)
        resp = client.detect_document_text(Document={"Bytes": buf.getvalue()})
        lines = [
            b.get("Text", "")
            for b in resp.get("Blocks", [])
            if b.get("BlockType") == "LINE"
        ]
        if lines:
            print(f"{OK} textract detect_document_text -> {lines!r}")
        else:
            # Reached the API fine; the default PIL font is just tiny/thin, so
            # a blank read here is not necessarily a problem.
            print(f"{OK} textract reachable (no text read from the test image)")
    except (ClientError, BotoCoreError) as e:
        fail("textract", e)


def check_s3() -> None:
    if not BUCKET:
        fail("s3", "STORAGE_BUCKET is not set in backend/.env")
        return
    key = f"{PREFIX}/_preflight/{uuid.uuid4()}.txt" if PREFIX else f"_preflight/{uuid.uuid4()}.txt"
    try:
        s3 = boto3.client("s3", region_name=REGION)
        s3.head_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"preflight", ContentType="text/plain")
        got = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        assert got == b"preflight", "round-tripped bytes did not match"
        s3.delete_object(Bucket=BUCKET, Key=key)
        print(f"{OK} s3 put/get/delete on s3://{BUCKET}/{PREFIX}")
    except (ClientError, BotoCoreError, AssertionError) as e:
        fail("s3", e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--image",
        action="store_true",
        help="also test BEDROCK_IMAGE_MODEL (generates and bills one image)",
    )
    args = ap.parse_args()

    check_identity()
    list_models()
    check_converse(MODEL_DEFAULT, "default")
    check_converse(MODEL_FALLBACK, "fallback")
    if IMAGE_REGION != REGION:
        list_image_models()
    if args.image:
        check_image()
    else:
        print(f"{INFO} image model not tested (pass --image to bill one test image)")
    check_textract()
    check_s3()

    print()
    if failures:
        print(f"{FAIL} {len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print(f"{OK} all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
