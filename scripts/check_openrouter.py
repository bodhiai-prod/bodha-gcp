"""Preflight check for the OpenRouter providers — diagrams by default, text with --text.

Verifies, in order: config present -> key accepted and funded -> model serves
the images endpoint -> one real image generated. Run it before starting the
backend, and before any load test. Inside the app generate_diagram() swallows
every failure and returns None, so an unfunded account or a wrong model slug
looks identical to "this question didn't need a diagram" — this script is where
the difference is visible.

    python scripts/check_openrouter.py
    python scripts/check_openrouter.py --save out.png   # keep the test image
    python scripts/check_openrouter.py --prompt "a labelled cross-section of a leaf"
    python scripts/check_openrouter.py --text           # check the TEXT models

--text is the check to run before flipping TEXT_PROVIDER: it resolves both model
slugs and makes one real call, so a typo'd slug or a model your account cannot
reach fails here instead of on the first question paper someone generates.

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

TEXT_PROVIDER = os.environ.get("TEXT_PROVIDER", "bedrock").strip().lower()
TEXT_MODEL = os.environ.get("OPENROUTER_TEXT_MODEL_DEFAULT", "openai/gpt-5.6")
TEXT_FALLBACK = os.environ.get(
    "OPENROUTER_TEXT_MODEL_FALLBACK", "anthropic/claude-opus-5"
)
TEXT_TIMEOUT = float(os.environ.get("OPENROUTER_TEXT_TIMEOUT", "150"))
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


# --- text -------------------------------------------------------------------
# The text path fails LOUDLY inside the app (chat_complete raises after
# exhausting the chain, and the paper is marked failed), so unlike the diagram
# checks above these are about catching a bad slug before it costs anyone a
# generation — not about surfacing a silent failure.

TEXT_PROBE = (
    "Reply with exactly this JSON and nothing else: "
    '{"ok": true, "provider": "openrouter"}'
)


def check_text_config() -> bool:
    print(f"{INFO} TEXT_PROVIDER: {TEXT_PROVIDER}")
    if TEXT_PROVIDER != "openrouter":
        print(
            f"{WARN} provider is not 'openrouter' — the app will still use "
            f"{TEXT_PROVIDER}.\n        This script tests OpenRouter regardless."
        )
    if not API_KEY:
        print(f"{FAIL} missing in backend/.env: OPENROUTER_API_KEY")
        return False
    print(f"{OK} key:      {API_KEY[:10]}…{API_KEY[-4:]} ({len(API_KEY)} chars)")
    print(f"{OK} default:  {TEXT_MODEL}")
    print(f"{OK} fallback: {TEXT_FALLBACK}")
    if TEXT_MODEL == TEXT_FALLBACK:
        print(
            f"{WARN} both slots hold the same model, so the fallback is a plain\n"
            "        retry rather than a second opinion."
        )
    print(f"{INFO} timeout={TEXT_TIMEOUT:.0f}s per call "
          f"(chat_complete allows {TEXT_TIMEOUT + 15:.0f}s)")
    return True


def _check_one_text_model(model: str) -> bool:
    """Confirm the slug exists and serves chat. A wrong slug 404s here."""
    url = f"{BASE_URL}/models/{model}/endpoints"
    try:
        r = _session().get(url, timeout=30)
    except requests.RequestException as e:
        print(f"{FAIL} could not reach {url}: {e}")
        return False

    if r.status_code == 404:
        print(
            f"{FAIL} '{model}' is not a model on OpenRouter.\n"
            "        Check the slug at https://openrouter.ai/models"
        )
        return False
    if r.status_code != 200:
        print(f"{WARN} {model}: discovery returned {r.status_code}; continuing anyway")
        return True

    d = (r.json() or {}).get("data") or {}
    endpoints = d.get("endpoints") or []
    providers = [e.get("provider_name") for e in endpoints if e.get("provider_name")]
    ctx = max((e.get("context_length") or 0) for e in endpoints) if endpoints else 0
    print(f"{OK} {model}: served by {', '.join(providers) or 'unknown'}")
    if ctx:
        print(f"{INFO}   context {ctx:,} tokens")
    return True


def check_text_models() -> bool:
    ok = _check_one_text_model(TEXT_MODEL)
    # Check the fallback even if the default failed: knowing whether BOTH slugs
    # are wrong or only one is is the difference between a typo and a bad plan.
    return _check_one_text_model(TEXT_FALLBACK) and ok


def check_text_chat() -> bool:
    """One real call against the default model, shaped like the app's."""
    payload = {
        "model": TEXT_MODEL,
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": TEXT_PROBE},
        ],
        "max_tokens": 200,
        "temperature": 0,
    }
    started = time.perf_counter()
    try:
        r = _session().post(
            f"{BASE_URL}/chat/completions", json=payload, timeout=(10, TEXT_TIMEOUT)
        )
    except requests.RequestException as e:
        print(f"{FAIL} chat request failed: {e}")
        return False
    elapsed = time.perf_counter() - started

    if r.status_code != 200:
        print(f"{FAIL} chat returned {r.status_code}: {r.text[:300]}")
        return False

    body = r.json()
    # An upstream failure comes back as 200 with an error object, which is why
    # llm_adapter._chat_openrouter_sync checks for it too.
    if body.get("error"):
        print(f"{FAIL} chat returned an error object: {str(body['error'])[:300]}")
        return False

    choices = body.get("choices") or []
    if not choices:
        print(f"{FAIL} no choices in the response: {str(body)[:300]}")
        return False

    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        print(
            f"{FAIL} empty content — llm_adapter treats this as a failed attempt\n"
            f"        and moves to the fallback. Raw: {str(message)[:300]}"
        )
        return False

    finish = choices[0].get("finish_reason") or ""
    usage = body.get("usage") or {}
    inp = usage.get("prompt_tokens") or 0
    out = usage.get("completion_tokens") or 0
    print(f"{OK} replied in {elapsed:.1f}s (finish_reason={finish or 'unset'})")
    print(f"{INFO} usage: in={inp} out={out}")
    cost = usage.get("cost")
    if cost is not None:
        # A paper is ~16k output tokens, so scale the probe to something useful.
        print(f"{INFO} ${cost:.6f} for this probe")
    print(f"{INFO} content: {content.strip()[:120]}")

    if finish == "length":
        print(
            f"{WARN} truncated at 200 tokens — expected for this probe, but note\n"
            "        llm_adapter maps finish_reason=length to Bedrock's 'max_tokens'\n"
            "        so parse_json_response's salvage path still applies."
        )
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt to test with")
    ap.add_argument("--save", metavar="PATH", help="write the generated image here")
    ap.add_argument(
        "--text",
        action="store_true",
        help="check the TEXT models (TEXT_PROVIDER) instead of the image model",
    )
    args = ap.parse_args()

    if args.text:
        for check in (check_text_config, check_key, check_text_models):
            if not check():
                return 1
        if not check_text_chat():
            return 1
        print(f"\n{OK} all checks passed")
        return 0

    for check in (check_config, check_key, check_model):
        if not check():
            return 1
    if not check_image(args.prompt, args.save):
        return 1

    print(f"\n{OK} all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
