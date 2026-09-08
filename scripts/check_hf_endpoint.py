"""Preflight check for the Hugging Face diagram endpoint.

Verifies, in order: config present -> endpoint reachable -> token accepted ->
one real image generated. Run it before starting the backend. Inside the app
generate_diagram() swallows every failure and returns None, so a broken
endpoint looks identical to "this question didn't need a diagram" — this script
is where the difference is visible.

    python scripts/check_hf_endpoint.py
    python scripts/check_hf_endpoint.py --save out.png   # keep the test image
    python scripts/check_hf_endpoint.py --prompt "a labelled circuit diagram"

A cold (scaled-to-zero) endpoint can take several minutes on the first call.
That is reported as it happens rather than looking like a hang.

Exit code is 0 only if every check passes.
"""
from __future__ import annotations

import argparse
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
ENDPOINT = os.environ.get("HF_ENDPOINT_URL", "").rstrip("/")
TOKEN = os.environ.get("HF_API_TOKEN", "")
MODEL_LABEL = os.environ.get("HF_IMAGE_MODEL", "hf-endpoint")
STEPS = int(os.environ.get("HF_IMAGE_STEPS", "30"))
GUIDANCE = float(os.environ.get("HF_IMAGE_GUIDANCE", "7.0"))
SIZE = int(os.environ.get("HF_IMAGE_SIZE", "1024"))
COLD_TIMEOUT = float(os.environ.get("HF_COLD_START_TIMEOUT", "300"))
READ_TIMEOUT = float(os.environ.get("HF_READ_TIMEOUT", "90"))

OK, FAIL, INFO, WARN = "[ ok ]", "[FAIL]", "[ .. ]", "[warn]"

DEFAULT_PROMPT = (
    "black and white line art diagram, a simple triangle with three vertices, "
    "simple clean outlines, monochrome, flat 2D, white background, unlabelled"
)


def check_config() -> bool:
    print(f"{INFO} IMAGE_PROVIDER: {PROVIDER}")
    if PROVIDER != "huggingface":
        print(
            f"{WARN} provider is not 'huggingface' — the app will still use Bedrock.\n"
            "        This script tests the HF endpoint regardless."
        )
    missing = [
        n for n, v in (("HF_ENDPOINT_URL", ENDPOINT), ("HF_API_TOKEN", TOKEN)) if not v
    ]
    if missing:
        print(f"{FAIL} missing in backend/.env: {', '.join(missing)}")
        return False
    # Show enough of the URL to confirm it's the right endpoint, never the token.
    print(f"{OK} endpoint: {ENDPOINT}")
    print(f"{OK} token:    {TOKEN[:6]}…{TOKEN[-4:]} ({len(TOKEN)} chars)")
    print(
        f"{INFO} params:   steps={STEPS} guidance={GUIDANCE} size={SIZE}x{SIZE} "
        f"(label '{MODEL_LABEL}')"
    )
    if STEPS > 8:
        print(
            f"{INFO} note: {STEPS} steps suits SDXL/SD3-class models. Turbo and LCM\n"
            "        variants want 1-4 — check your model card."
        )
    return True


def check_image(prompt: str, save: str | None) -> bool:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "image/png",
            "x-wait-for-model": "true",
        }
    )
    payload = {
        "inputs": prompt,
        "parameters": {
            "width": SIZE,
            "height": SIZE,
            "num_inference_steps": STEPS,
            "guidance_scale": GUIDANCE,
        },
    }

    started = time.perf_counter()
    deadline = started + COLD_TIMEOUT
    delay, attempt, announced = 2.0, 0, False

    while True:
        attempt += 1
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            print(f"{FAIL} endpoint never became ready within {COLD_TIMEOUT:.0f}s")
            return False
        try:
            resp = session.post(
                ENDPOINT,
                json=payload,
                timeout=(10, max(5.0, min(remaining, READ_TIMEOUT))),
            )
        except requests.RequestException as e:
            print(f"{FAIL} could not reach endpoint: {e}")
            return False

        if resp.status_code == 200:
            break
        if resp.status_code in (401, 403):
            print(f"{FAIL} credentials rejected ({resp.status_code}) — check HF_API_TOKEN")
            return False
        if resp.status_code in (502, 503, 504):
            if not announced:
                print(
                    f"{INFO} endpoint is cold ({resp.status_code}); waiting up to "
                    f"{COLD_TIMEOUT:.0f}s for it to load…"
                )
                announced = True
            time.sleep(min(delay, max(0.0, deadline - time.perf_counter())))
            delay = min(delay * 1.5, 15.0)
            continue
        print(f"{FAIL} endpoint returned {resp.status_code}: {resp.text[:300]}")
        return False

    elapsed = time.perf_counter() - started
    ctype = (resp.headers.get("content-type") or "").lower()
    data = resp.content

    if "application/json" in ctype or not data:
        print(f"{FAIL} expected an image, got {ctype or 'empty body'}: {resp.text[:300]}")
        return False

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        fmt, dims = img.format, f"{img.size[0]}x{img.size[1]}"
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} response was not a decodable image: {e}")
        return False

    print(
        f"{OK} generated {dims} {fmt}, {len(data) / 1e6:.2f} MB in {elapsed:.1f}s "
        f"({attempt} attempt(s))"
    )
    if fmt != "PNG":
        print(
            f"{INFO} endpoint returned {fmt}; llm_adapter._normalize_png converts it,\n"
            "        so storage and PDF embedding are unaffected."
        )
    if elapsed > 20:
        print(
            f"{INFO} {elapsed:.0f}s per image is slow for a warm endpoint. Expect ~5x\n"
            f"        that for a 5-diagram paper — check the GPU tier or lower steps."
        )

    if save:
        Path(save).write_bytes(data)
        print(f"{OK} saved to {save}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt to test with")
    ap.add_argument("--save", metavar="PATH", help="write the generated image here")
    args = ap.parse_args()

    if not check_config():
        return 1
    if not check_image(args.prompt, args.save):
        return 1

    print(f"\n{OK} all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
