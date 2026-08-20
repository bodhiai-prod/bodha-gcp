"""Preflight check for the OpenRouter diagram provider.

Verifies, in order: config present -> key accepted and funded -> model serves
the images endpoint -> one real image generated. Run it before starting the
backend, and before any load test. Inside the app generate_diagram() swallows
every failure and returns None, so an unfunded account or a wrong model slug
looks identical to "this question didn't need a diagram" — this script is where
the difference is visible.

    python scripts/check_openrouter.py
    python scripts/check_openrouter.py --save out.png   # keep the test image
    python scripts/check_openrouter.py --prompt "a labelled cross-section of a leaf"

Saving the image is the point of the exercise for this provider: it is the only
way to confirm the LABELS are legible and spelled correctly, which is the reason
to prefer Gemini over the diffusion providers.

Exit code is 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "backend" / ".env")

PROVIDER = os.environ.get("IMAGE_PROVIDER", "bedrock").strip().lower()
BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image")
ASPECT_RATIO = os.environ.get("OPENROUTER_ASPECT_RATIO", "1:1")
TIMEOUT = float(os.environ.get("OPENROUTER_TIMEOUT", "90"))
CONCURRENCY = int(os.environ.get("DIAGRAM_MAX_CONCURRENCY", "12"))

OK, FAIL, INFO, WARN = "[ ok ]", "[FAIL]", "[ .. ]", "[warn]"

DEFAULT_PROMPT = (
    "A black and white line drawing for a school exam paper: a cross-section of "
    "a plant cell. Draw it as a clean, simple, flat 2D diagram with plain black "
    "outlines on a plain white background, in the style of a scientific textbook "
    "figure. Label the parts clearly with short printed text and leader lines, "
    "using correct spelling. No colour, no shading, no 3D, no watermark."
)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    )
    return s


def check_config() -> bool:
    print(f"{INFO} IMAGE_PROVIDER: {PROVIDER}")
    if PROVIDER != "openrouter":
        print(
            f"{WARN} provider is not 'openrouter' — the app will still use "
            f"{PROVIDER}.\n        This script tests OpenRouter regardless."
        )
    if not API_KEY:
        print(f"{FAIL} missing in backend/.env: OPENROUTER_API_KEY")
        return False
    # Enough of the key to confirm which one is loaded, never the whole thing.
    print(f"{OK} key:   {API_KEY[:10]}…{API_KEY[-4:]} ({len(API_KEY)} chars)")
    print(f"{OK} model: {MODEL}")
    print(f"{INFO} aspect_ratio={ASPECT_RATIO} timeout={TIMEOUT:.0f}s")
    print(f"{INFO} DIAGRAM_MAX_CONCURRENCY={CONCURRENCY} (process-wide ceiling)")
    if MODEL.endswith(":free"):
        print(
            f"{WARN} ':free' models are capped at 20 requests/min platform-wide.\n"
            "        That will not sustain 40-50 diagrams/min."
        )
    return True


def check_key() -> bool:
    """Credit state. An unfunded key authenticates fine and then refuses every
    paid model, which is the confusing failure this catches."""
    try:
        r = _session().get(f"{BASE_URL}/key", timeout=30)
    except requests.RequestException as e:
        print(f"{FAIL} could not reach {BASE_URL}: {e}")
        return False

    if r.status_code in (401, 403):
        print(f"{FAIL} key rejected ({r.status_code}). Check OPENROUTER_API_KEY.")
        return False
    if r.status_code != 200:
        print(f"{FAIL} GET /key returned {r.status_code}: {r.text[:200]}")
        return False

    d = (r.json() or {}).get("data") or {}
    limit, used = d.get("limit"), d.get("usage")
    print(f"{OK} key accepted (usage so far: ${used or 0:.4f})")
    if d.get("is_free_tier"):
        print(
            f"{WARN} account is on the free tier — no credits purchased.\n"
            "        Paid image models will refuse. Add credits before load testing."
        )
    if limit is not None:
        print(f"{INFO} credit cap on this key: ${limit}")
    return True


def check_model() -> bool:
    """Confirms the slug serves the images endpoint and reports what it accepts.
    A model that only does chat will 404 here rather than at generation time."""
    url = f"{BASE_URL}/images/models/{MODEL}/endpoints"
    try:
        r = _session().get(url, timeout=30)
    except requests.RequestException as e:
        print(f"{FAIL} could not reach {url}: {e}")
        return False

    if r.status_code == 404:
        print(
            f"{FAIL} '{MODEL}' does not serve the images endpoint.\n"
            "        Check the slug at https://openrouter.ai/models"
        )
        return False
    if r.status_code != 200:
        print(f"{WARN} endpoint discovery returned {r.status_code}; continuing anyway")
        return True

    d = (r.json() or {}).get("data") or {}
    endpoints = d.get("endpoints") or []
    providers = [e.get("provider_name") for e in endpoints if e.get("provider_name")]
    params = sorted({p for e in endpoints for p in (e.get("supported_parameters") or [])})
    print(f"{OK} served by: {', '.join(providers) or 'unknown'}")
    if len(providers) > 1:
        print(
            f"{INFO} more than one upstream — OpenRouter can fall back between them\n"
            "        under burst. Do not pin provider.only."
        )
    if params:
        print(f"{INFO} accepts: {', '.join(params)}")
        if "aspect_ratio" not in params:
            print(
                f"{WARN} this model ignores aspect_ratio; figures may not be square.\n"
                "        pdf_utils scales to fit either way, so this is cosmetic."
            )
    return True


def check_image(prompt: str, save: str | None) -> bool:
    payload = {"model": MODEL, "prompt": prompt, "n": 1, "aspect_ratio": ASPECT_RATIO}
    started = time.perf_counter()
    try:
        r = _session().post(f"{BASE_URL}/images", json=payload, timeout=(10, TIMEOUT))
    except requests.RequestException as e:
        print(f"{FAIL} image request failed: {e}")
        return False
    elapsed = time.perf_counter() - started

    if r.status_code == 429:
        print(
            f"{FAIL} rate limited (429) on a single request. Either the account is\n"
            "        unfunded or the upstream is saturated."
        )
        return False
    if r.status_code != 200:
        print(f"{FAIL} generation returned {r.status_code}: {r.text[:300]}")
        return False

    body = r.json()
    items = body.get("data") or []
    if not items or not items[0].get("b64_json"):
        print(f"{FAIL} no image in the response: {str(body)[:300]}")
        return False

    data = base64.b64decode(items[0]["b64_json"])
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            dims, fmt = f"{im.size[0]}x{im.size[1]}", im.format
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} response was not a decodable image: {e}")
        return False

    print(
        f"{OK} generated {dims} {fmt}, {len(data) / 1e6:.2f} MB in {elapsed:.1f}s"
    )
    if fmt != "PNG":
        print(
            f"{INFO} model returned {fmt}; llm_adapter._normalize_png converts it,\n"
            "        so storage and PDF embedding are unaffected."
        )

    usage = body.get("usage") or {}
    out_tokens = usage.get("completion_tokens") or 0
    cost = usage.get("cost")
    if out_tokens or cost is not None:
        line = f"{INFO} usage: {out_tokens} output tokens"
        if cost is not None:
            line += f", ${cost:.4f} this image"
            # The number that decides whether this provider is affordable at the
            # target rate, so state it rather than leaving it to be worked out.
            print(f"{line}  (~${cost * 50 * 60:.0f}/hr at 50 diagrams/min)")
        else:
            print(line)

    # Little's law: at 50/min a 10s image needs ~9 concurrent to keep up.
    needed = (50 / 60) * elapsed
    if needed > CONCURRENCY:
        print(
            f"{WARN} at {elapsed:.0f}s/image, 50 diagrams/min needs ~{needed:.0f}\n"
            f"        concurrent but DIAGRAM_MAX_CONCURRENCY is {CONCURRENCY}."
        )

    if save:
        Path(save).write_bytes(data)
        print(f"{OK} saved to {save}")
        print(
            f"{INFO} open it and check the LABELS are legible and correctly spelled —\n"
            "        that is the whole reason to use this provider."
        )
    else:
        print(f"{INFO} re-run with --save out.png to inspect the labels.")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt to test with")
    ap.add_argument("--save", metavar="PATH", help="write the generated image here")
    args = ap.parse_args()

    for check in (check_config, check_key, check_model):
        if not check():
            return 1
    if not check_image(args.prompt, args.save):
        return 1

    print(f"\n{OK} all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
