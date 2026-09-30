"""LLM adapter for text and diagram generation, backed by Bedrock, Vertex, or OpenRouter.

Replaces the original universal-key adapter. The public surface is
unchanged — `chat_complete`, `chain_for_exam`, `generate_diagram`, and
`parse_json_response` keep the same signatures and semantics — so no call site
in server.py / workers.py / competitive_routes.py needed to change.

Configuration (all via environment / backend/.env):
    AWS_REGION                 region for bedrock-runtime
    BEDROCK_MODEL_DEFAULT      primary Claude model id (or inference profile id)
    BEDROCK_MODEL_FALLBACK     secondary model id, used when the primary fails
    BEDROCK_IMAGE_MODEL        image model for question diagrams (Nova Canvas)
    BEDROCK_MAX_TOKENS         max output tokens per call
    BEDROCK_TEMPERATURE        sampling temperature
    ENABLE_DIAGRAMS            "false" disables diagram generation entirely
    TEXT_PROVIDER              bedrock | openrouter (text only)
    IMAGE_PROVIDER             bedrock | huggingface | vertex | openrouter (images only)
    OPENROUTER_API_KEY         key for both OpenRouter providers
    OPENROUTER_TEXT_MODEL_DEFAULT   e.g. openai/gpt-5.6
    OPENROUTER_TEXT_MODEL_FALLBACK  e.g. anthropic/claude-opus-5
    VERTEX_IMAGE_MODEL         e.g. gemini-3.1-flash-lite-image
    OPENROUTER_IMAGE_MODEL     optional inactive image-provider fallback
    DIAGRAM_MAX_CONCURRENCY    process-wide ceiling on diagrams in flight

AWS credentials are resolved by boto3 in the usual order (env vars, shared
credentials file, AWS_PROFILE, instance role).
"""
import asyncio
import base64
import json
import logging
import os
import re
import threading
import time
from typing import Any

from review_budget import current as current_review_budget, reserve_persisted_provider_attempt, reserve_thread_provider_retry

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


class MalformedProviderResponseError(ValueError):
    """The provider returned text that cannot safely be interpreted as JSON.

    Callers use this distinction to retry a transport/format failure without
    confusing it with a validation failure in the generated paper itself.
    """

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Diagrams run in their own region: Nova Canvas was retired and the Stability
# text-to-image models are only offered in us-west-2. Text (Converse), Textract
# and S3 stay in AWS_REGION — Bedrock returns the image inline in the response
# body, so nothing has to move with it. Unset => same region as everything else.
BEDROCK_IMAGE_REGION = os.environ.get("BEDROCK_IMAGE_REGION", AWS_REGION)

MODEL_DEFAULT = os.environ.get(
    "BEDROCK_MODEL_DEFAULT", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)
MODEL_FALLBACK = os.environ.get(
    "BEDROCK_MODEL_FALLBACK", "us.anthropic.claude-opus-4-1-20250805-v1:0"
)
DIAGRAM_MODEL = os.environ.get("BEDROCK_IMAGE_MODEL", "stability.sd3-5-large-v1:0")

MAX_TOKENS = int(os.environ.get("BEDROCK_MAX_TOKENS", "16384"))
TEMPERATURE = float(os.environ.get("BEDROCK_TEMPERATURE", "0.7"))
ENABLE_DIAGRAMS = os.environ.get("ENABLE_DIAGRAMS", "true").lower() not in (
    "0",
    "false",
    "no",
)

# --- providers ---------------------------------------------------------------
# Text and images switch independently: the two workloads have different model
# requirements and different billing, and in practice we have run a Bedrock text
# model alongside a Vertex image model. Text and images remain independently
# switchable because they have different billing and availability requirements.
#
# The model ids per provider live further down (BEDROCK_MODEL_* above,
# OPENROUTER_TEXT_MODEL_* below); the active pair is resolved once into
# TEXT_MODEL_DEFAULT / TEXT_MODEL_FALLBACK where PROVIDER_CHAIN is built.
TEXT_PROVIDER = os.environ.get("TEXT_PROVIDER", "openrouter").strip().lower()
IMAGE_PROVIDER = os.environ.get("IMAGE_PROVIDER", "vertex").strip().lower()

# Hugging Face dedicated Inference Endpoint. The text-to-image task schema is
# the same for every diffusion model behind an endpoint, so the model choice
# lives entirely in these values — steps and guidance are what actually differ
# between SDXL, FLUX and the turbo/LCM variants (which want 1-4 steps, not 30).
HF_ENDPOINT_URL = os.environ.get("HF_ENDPOINT_URL", "").rstrip("/")
HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")
# Label only — used for the usage CSV so token_report shows which model served
# each diagram. Never sent to the endpoint; the URL already identifies it.
HF_IMAGE_MODEL = os.environ.get("HF_IMAGE_MODEL", "hf-endpoint")
HF_IMAGE_STEPS = int(os.environ.get("HF_IMAGE_STEPS", "30"))
HF_IMAGE_GUIDANCE = float(os.environ.get("HF_IMAGE_GUIDANCE", "7.0"))
HF_IMAGE_SIZE = int(os.environ.get("HF_IMAGE_SIZE", "1024"))
# Waking a diffusion model from scale-to-zero takes minutes, not seconds; a
# warm endpoint answers in a few. Two budgets rather than one so a steady-state
# failure fails fast instead of hanging for the full cold-start window.
HF_COLD_START_TIMEOUT = float(os.environ.get("HF_COLD_START_TIMEOUT", "300"))
HF_READ_TIMEOUT = float(os.environ.get("HF_READ_TIMEOUT", "90"))

# Vertex AI Gemini image generation uses the VM's Application Default
# Credentials.  The image models are global publisher models, while the rest of
# this application's regional GCP resources continue using GCP_REGION.
VERTEX_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "").strip()
VERTEX_IMAGE_LOCATION = os.environ.get("VERTEX_IMAGE_LOCATION", "global").strip()
VERTEX_IMAGE_MODEL = os.environ.get(
    "VERTEX_IMAGE_MODEL", "gemini-3.1-flash-lite-image"
)
VERTEX_IMAGE_ASPECT_RATIO = os.environ.get("VERTEX_IMAGE_ASPECT_RATIO", "1:1")
VERTEX_TIMEOUT = float(os.environ.get("VERTEX_TIMEOUT", "90"))
VERTEX_MAX_RETRIES = int(os.environ.get("VERTEX_MAX_RETRIES", "3"))

# OpenRouter, used for the Gemini Flash image models. Unlike the two diffusion
# providers this is an instruction-following model behind a plain hosted API:
# there is no endpoint to keep warm, no steps/guidance to tune, and — the
# reason it is worth having — it renders legible text, so its diagrams can be
# labelled (see _diagram_prompt).
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).rstrip("/")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Text models, read only when TEXT_PROVIDER is openrouter. Two genuinely
# different vendors on purpose: the Bedrock pair currently has the same id in
# both slots, which makes its fallback a no-op retry rather than a real one.
OPENROUTER_TEXT_MODEL_DEFAULT = os.environ.get(
    "OPENROUTER_TEXT_MODEL_DEFAULT", "google/gemini-3.5-flash-lite"
)
OPENROUTER_TEXT_MODEL_FALLBACK = os.environ.get(
    "OPENROUTER_TEXT_MODEL_FALLBACK", "google/gemini-3.8-flash"
)
# Deliberately BELOW chat_complete's per-call budget (see _text_timeout): we
# want a slow call to surface as a real HTTP error — recorded, attributed to a
# model — rather than be abandoned anonymously by asyncio.wait_for.
OPENROUTER_TEXT_TIMEOUT = float(os.environ.get("OPENROUTER_TEXT_TIMEOUT", "150"))
# Default to the Bedrock values so neither has to be set; separate vars because
# a per-provider ceiling is the first thing you reach for when one model
# truncates papers and the other does not.
OPENROUTER_TEXT_MAX_TOKENS = int(
    os.environ.get("OPENROUTER_TEXT_MAX_TOKENS", str(MAX_TOKENS))
)
OPENROUTER_TEXT_TEMPERATURE = float(
    os.environ.get("OPENROUTER_TEXT_TEMPERATURE", str(TEMPERATURE))
)

OPENROUTER_IMAGE_MODEL = os.environ.get(
    "OPENROUTER_IMAGE_MODEL", "google/gemini-3.1-flash-lite-image"
)
# The Gemini image endpoints accept aspect_ratio, n and input_references, and
# nothing else — no negative_prompt, no output_format, no seed. "1:1" gives the
# square figure pdf_utils._make_diagram_image() fits into 100x90mm.
OPENROUTER_ASPECT_RATIO = os.environ.get("OPENROUTER_ASPECT_RATIO", "1:1")
OPENROUTER_TIMEOUT = float(os.environ.get("OPENROUTER_TIMEOUT", "90"))
OPENROUTER_MAX_RETRIES = int(os.environ.get("OPENROUTER_MAX_RETRIES", "3"))
# Optional attribution — shows this traffic under a recognisable name on the
# OpenRouter dashboard. Purely cosmetic; omitted from the headers when unset.
OPENROUTER_REFERER = os.environ.get("OPENROUTER_REFERER", "")
OPENROUTER_TITLE = os.environ.get("OPENROUTER_TITLE", "")

# Ceiling on diagrams in flight across the whole process, regardless of how many
# papers are generating at once. workers.generate_diagrams_for_paper() caps at 5
# PER PAPER and fires them with asyncio.gather, so ten concurrent papers mean a
# ~50-request burst rather than a steady stream — exactly the shape that trips a
# provider rate limit. generate_diagram() swallows failures, so without this the
# symptom is papers quietly shipping without figures.
DIAGRAM_MAX_CONCURRENCY = int(os.environ.get("DIAGRAM_MAX_CONCURRENCY", "2"))

# Shared by the two diffusion providers. Text is suppressed outright rather than
# merely de-duplicated: diffusion models cannot render legible words, and asking
# for labels produces confident gibberish ("Chalol LealExinmimale") that looks
# far worse on a printed paper than an unlabelled figure. The question text
# carries the terminology instead. Not used by OpenRouter — those endpoints take
# no negative prompt, and Gemini can spell.
_NEGATIVE_PROMPT = (
    "color, shading, gradients, 3d, render, photo, realistic, texture, "
    "watermark, text, words, letters, labels, captions, "
    "solution, answer, calculation, working, measurements, numbers"
)

# --- load-testing stubs ------------------------------------------------------
# LOAD_TEST_STUB makes chat_complete()/generate_diagram() return canned data
# instead of calling Bedrock, so a load test measures OUR hardware rather than
# AWS latency, cost, and account throttling. OFF unless explicitly enabled —
# an unset env var leaves behaviour exactly as it was.
#
# LOAD_TEST_STUB_LATENCY_MS controls the fake think-time. Default 30s
# approximates a real Bedrock call; drop it to ~200 to measure raw request
# throughput instead of concurrent in-flight capacity.
LOAD_TEST_STUB = os.environ.get("LOAD_TEST_STUB", "").lower() in ("1", "true", "yes")
STUB_LATENCY_MS = int(os.environ.get("LOAD_TEST_STUB_LATENCY_MS", "30000"))

if LOAD_TEST_STUB:
    logger.warning(
        "=" * 70
        + "\nLOAD_TEST_STUB IS ENABLED - Bedrock is NOT being called.\n"
        f"chat_complete()/generate_diagram() return canned data after "
        f"{STUB_LATENCY_MS}ms.\nGenerated papers are MEANINGLESS. Never run a real "
        "deployment like this.\n" + "=" * 70
    )

# Canned paper matching the schema workers.generate_paper_background expects:
# `instructions` plus `sections[].questions[]`. One question sets
# needs_diagram=true so the diagram path is exercised without dominating the run.
_STUB_PAPER = {
    "instructions": "Answer all questions. [LOAD TEST STUB — not a real paper]",
    "sections": [
        {
            "title": "Section A",
            "questions": [
                {
                    "question": f"Stub question {i} for load testing.",
                    "topic": "Stub Topic",
                    "type": "concept",
                    "format": "short_answer",
                    "difficulty": "medium",
                    "marks": 2,
                    "needs_diagram": False,
                }
                for i in range(1, 11)
            ]
            + [
                {
                    "question": "Stub question with a figure.",
                    "topic": "Stub Topic",
                    "type": "application",
                    "format": "short_answer",
                    "difficulty": "medium",
                    "marks": 5,
                    "needs_diagram": True,
                    "diagram_description": "a simple labelled triangle",
                }
            ],
        }
    ],
}

# Canned answer-key payload for the solution path, which expects {"answers": [...]}
# keyed by question_id. Question ids are unknown here, so an empty list is
# returned and the caller's own fallback fills in placeholders.
_STUB_SOLUTION = {"answers": []}

_STUB_TOPICS = {
    "topics": [
        {"name": f"Stub Topic {i}", "subtopics": ["Stub Subtopic A", "Stub Subtopic B"]}
        for i in range(1, 9)
    ]
}


def _stub_response(system_message: str) -> str:
    """Pick a canned payload matching what the caller parses.

    chat_complete() serves four different callers (paper generation, answer
    keys, topic extraction, question-paper import). Returning paper JSON to all
    of them would make the non-paper routes fail in ways that look like load
    failures, so route on the caller's system prompt.
    """
    s = system_message.lower()
    if "answer key" in s:
        return json.dumps(_STUB_SOLUTION)
    if "analyse textbook" in s:
        return json.dumps(_STUB_TOPICS)
    return json.dumps(_STUB_PAPER)

# Smallest valid PNG (1x1 transparent) — enough for the S3 save and PIL open in
# pdf_utils._make_diagram_image to succeed.
_STUB_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

# The active text pair. Every chain below is built from these two names rather
# than from the provider-specific ids, so switching TEXT_PROVIDER re-points all
# routing at once and chat_complete never has to know which provider it is on.
if TEXT_PROVIDER == "openrouter":
    TEXT_MODEL_DEFAULT = OPENROUTER_TEXT_MODEL_DEFAULT
    TEXT_MODEL_FALLBACK = OPENROUTER_TEXT_MODEL_FALLBACK
else:
    TEXT_MODEL_DEFAULT = MODEL_DEFAULT
    TEXT_MODEL_FALLBACK = MODEL_FALLBACK

# Default chain: try the fast/cheap model first, fall through to the stronger
# one. Every id comes from the environment so they can be swapped without code
# changes (model availability differs per AWS account and region, and per
# OpenRouter account for the hosted models).
PROVIDER_CHAIN: list[str] = [TEXT_MODEL_DEFAULT, TEXT_MODEL_FALLBACK]

# Per-task model routing for competitive-exam papers. The first model is tried
# first; the rest are fallbacks with the same retry behaviour as the default
# chain. Exams that reward deeper reasoning lead with the stronger model.
EXAM_PROVIDER_CHAINS: dict[str, list[str]] = {
    "JEE_MAINS": [TEXT_MODEL_DEFAULT, TEXT_MODEL_FALLBACK],
    "JEE_ADV": [TEXT_MODEL_FALLBACK, TEXT_MODEL_DEFAULT],
    "UPSC": [TEXT_MODEL_FALLBACK, TEXT_MODEL_DEFAULT],
    "CAT": [TEXT_MODEL_DEFAULT, TEXT_MODEL_FALLBACK],
    "NEET": [TEXT_MODEL_DEFAULT, TEXT_MODEL_FALLBACK],
}


def chain_for_exam(exam_type: str | None) -> list[str]:
    """Return the model chain for an exam_type, or the default chain."""
    if not exam_type:
        return PROVIDER_CHAIN
    return EXAM_PROVIDER_CHAINS.get(exam_type.upper(), PROVIDER_CHAIN)


# Exponential backoff settings for transient upstream errors. Botocore's own
# retries are disabled (see _client) so this layer stays in charge — it fails
# fast on non-transient errors and moves to the next model instead of burning
# minutes on a request that will never succeed.
RETRY_MAX_ATTEMPTS = 3  # per model
RETRY_BASE_DELAY_SEC = 2.0
RETRY_BACKOFF = 2.0  # 2s, 4s, 8s

# HTTP statuses from OpenRouter worth another attempt. 408 and 409 are here
# because OpenRouter returns them for upstream queue timeouts and races, not for
# anything malformed in what we sent.
_OPENROUTER_RETRYABLE = frozenset({408, 409, 429, 500, 502, 503, 504})

# Stamped into the error message for those statuses so _is_transient() can
# recognise them. A tag rather than a bare "500" marker because that check runs
# over the whole message, where "500" would also match a token count like 1500.
_TRANSIENT_TAG = "transient upstream"


# Substrings that mark a TRANSIENT upstream error worth retrying. Anything
# else (auth, model-access, content-policy, malformed request) fails fast so we
# move to the next model quickly.
_TRANSIENT_MARKERS = (
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "timed out",
    "timeout",
    "connection error",
    "connection reset",
    "internal server error",
    "remote disconnected",
    "rate limit",
    "rate_limit",
    "429",
    "overloaded",
    # Bedrock / botocore exception names
    "throttlingexception",
    "modelnotreadyexception",
    "serviceunavailableexception",
    "modeltimeoutexception",
    "internalserverexception",
    "serviceunavailable",
    "endpointconnectionerror",
    "connecttimeouterror",
    "readtimeouterror",
    # OpenRouter statuses — see _OPENROUTER_RETRYABLE above.
    _TRANSIENT_TAG,
)


def _is_transient(err: BaseException) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


# Optional usage accounting. token_usage.install() registers these at startup;
# left unset every call below is a no-op and behaviour is byte-identical.
#
# Deliberately callbacks rather than `import token_usage` — this module imports
# nothing from the project, which is the only reason the offline tests can
# import it with no AWS configuration. Keep it that way.
_usage_sink = None
_image_usage_sink = None


def set_usage_sinks(on_tokens=None, on_image=None) -> None:
    """Register (or clear) the usage callbacks. See backend/token_usage.py."""
    global _usage_sink, _image_usage_sink
    _usage_sink, _image_usage_sink = on_tokens, on_image


_client_lock = threading.Lock()
_bedrock = None


def _client():
    """Lazily create (and cache) the bedrock-runtime client.

    Lazy so that importing this module never requires AWS configuration —
    the offline unit tests import `parse_json_response` from here.
    """
    global _bedrock
    if _bedrock is None:
        with _client_lock:
            if _bedrock is None:
                _bedrock = boto3.client(
                    "bedrock-runtime",
                    region_name=AWS_REGION,
                    config=Config(
                        # Our own retry layer above handles backoff; botocore
                        # retrying underneath would multiply the wall time.
                        retries={"max_attempts": 0, "mode": "standard"},
                        read_timeout=180,
                        connect_timeout=10,
                    ),
                )
    return _bedrock


_image_client_lock = threading.Lock()
_bedrock_image = None


def _image_client():
    """Lazily create (and cache) the bedrock-runtime client used for diagrams.

    Separate from _client() because image generation lives in a different
    region (see BEDROCK_IMAGE_REGION). When the two regions are equal this is
    just a second client object against the same endpoint — cheap, and it
    keeps the region split declarative rather than conditional.
    """
    global _bedrock_image
    if _bedrock_image is None:
        with _image_client_lock:
            if _bedrock_image is None:
                _bedrock_image = boto3.client(
                    "bedrock-runtime",
                    region_name=BEDROCK_IMAGE_REGION,
                    config=Config(
                        retries={"max_attempts": 0, "mode": "standard"},
                        read_timeout=180,
                        connect_timeout=10,
                    ),
                )
    return _bedrock_image


def _converse_sync(model_id: str, system_message: str, user_text: str) -> str:
    """Blocking Bedrock Converse call. Always run in a worker thread."""
    resp = _client().converse(
        modelId=model_id,
        system=[{"text": system_message}],
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
    )
    stop_reason = resp.get("stopReason")
    if stop_reason == "max_tokens":
        # parse_json_response() has a salvage path for exactly this case, but
        # it is worth knowing when a paper came back truncated.
        logger.warning(
            f"Bedrock {model_id} hit maxTokens ({MAX_TOKENS}); response is truncated"
        )
    if _usage_sink:
        # Recorded here rather than in chat_complete because this is the only
        # point that sees a call which asyncio.wait_for later abandons: the 165s
        # timeout there is shorter than botocore's 180s read_timeout, so the
        # request completes and bills even though its result is discarded.
        try:
            _usage_sink(
                model_id,
                resp.get("usage") or {},
                stop_reason=stop_reason or "",
                latency_ms=(resp.get("metrics") or {}).get("latencyMs", 0),
            )
        except Exception:  # noqa: BLE001 — accounting must never break generation
            logger.debug("usage sink failed", exc_info=True)
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    return "".join(b["text"] for b in blocks if "text" in b)


def _openrouter_text_content(message: dict) -> str:
    """Text out of one chat-completions message.

    `content` is a plain string for every model we route to, but the schema also
    permits the multipart list form, and a reasoning model can return null
    content with its output in `reasoning` instead. Handle all three rather than
    let a shape change surface as a silently empty paper.
    """
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    else:
        text = ""
    if not text.strip():
        reasoning = message.get("reasoning")
        if isinstance(reasoning, str):
            text = reasoning
    return text


def _chat_openrouter_sync(model_id: str, system_message: str, user_text: str) -> str:
    """Blocking OpenRouter chat-completions call. Always run in a worker thread.

    Same (model, system, user) -> text contract as _converse_sync, which is what
    lets chat_complete's retry-then-fall-through engine drive either provider
    without knowing the difference.

    Deliberately NO inner retry loop, unlike _generate_image_openrouter. That one
    has to retry itself because generate_diagram swallows failures; here the
    caller already retries with backoff and then advances the chain, so a second
    loop underneath would multiply the wall time the same way botocore's retries
    would (see _client). Retryable failures are instead marked with
    _TRANSIENT_TAG, which is what _is_transient() reads back out.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("TEXT_PROVIDER=openrouter but OPENROUTER_API_KEY is unset")

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": OPENROUTER_TEXT_MAX_TOKENS,
        "temperature": OPENROUTER_TEXT_TEMPERATURE,
    }

    started = time.perf_counter()
    try:
        # Session, auth and attribution headers are shared with the diagram
        # path; see _openrouter_session() below.
        resp = _openrouter_session().post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            timeout=(10, OPENROUTER_TEXT_TIMEOUT),
        )
    except Exception as e:  # noqa: BLE001 — requests.RequestException et al
        # Tagged rather than left to speak for itself: a socket failure against a
        # hosted API is transient by definition, but requests phrases them in
        # ways _is_transient has no marker for ("Max retries exceeded with url").
        raise RuntimeError(f"OpenRouter request failed ({_TRANSIENT_TAG}): {e}") from e
    latency_ms = int((time.perf_counter() - started) * 1000)

    if resp.status_code in (401, 403):
        # A bad key will never come good. Note the body, never the headers.
        raise RuntimeError(
            f"OpenRouter rejected credentials ({resp.status_code}). "
            "Check OPENROUTER_API_KEY."
        )
    if resp.status_code in _OPENROUTER_RETRYABLE:
        # No sleep and no loop here — chat_complete backs off and then advances
        # the chain. The tag is what tells it this one is worth re-attempting.
        raise RuntimeError(
            f"OpenRouter returned {resp.status_code} ({_TRANSIENT_TAG}): "
            f"{resp.text[:300]}"
        )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter returned {resp.status_code}: {resp.text[:300]}")

    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(
            f"OpenRouter returned a non-JSON body: {resp.text[:300]}"
        ) from None

    # An upstream provider failure arrives as HTTP 200 with an `error` object and
    # no choices, so the status code alone is not enough to call this a success.
    if body.get("error"):
        raise RuntimeError(f"OpenRouter error: {str(body['error'])[:300]}")

    choices = body.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    finish_reason = (choices[0].get("finish_reason") or "") if choices else ""

    # Normalised to Bedrock's vocabulary: token_usage.record_tokens keys its
    # "truncated" outcome on the literal string "max_tokens", and
    # parse_json_response's salvage path exists for exactly this case.
    stop_reason = "max_tokens" if finish_reason == "length" else finish_reason
    if stop_reason == "max_tokens":
        logger.warning(
            f"OpenRouter {model_id} hit max_tokens "
            f"({OPENROUTER_TEXT_MAX_TOKENS}); response is truncated"
        )

    if _usage_sink:
        # Recorded here rather than in chat_complete for the same reason as
        # _converse_sync: this is the only point that sees a call the caller's
        # timeout may later abandon, and an abandoned call still bills.
        try:
            usage = body.get("usage") or {}
            inp = int(usage.get("prompt_tokens") or 0)
            out = int(usage.get("completion_tokens") or 0)
            _usage_sink(
                model_id,
                # Bedrock's key names — record_tokens reads these, so keeping the
                # mapping here means token_usage.py stays provider-agnostic.
                {
                    "inputTokens": inp,
                    "outputTokens": out,
                    "totalTokens": int(usage.get("total_tokens") or (inp + out)),
                },
                stop_reason=stop_reason,
                latency_ms=latency_ms,
            )
        except Exception:  # noqa: BLE001 — accounting must never break generation
            logger.debug("usage sink failed", exc_info=True)

    # An empty string is a valid return: chat_complete treats it as a failed
    # attempt and moves on, which is the right response to a content filter.
    return _openrouter_text_content(message)


def _text_complete_sync(model_id: str, system_message: str, user_text: str) -> str:
    """Dispatch to the configured text provider. Always run in a worker thread.

    TEXT_PROVIDER is read here rather than captured by the caller so that tests
    can monkeypatch it, matching _generate_image_sync.
    """
    if TEXT_PROVIDER == "openrouter":
        return _chat_openrouter_sync(model_id, system_message, user_text)
    return _converse_sync(model_id, system_message, user_text)


def _text_timeout() -> float:
    """Per-call budget for chat_complete's asyncio.wait_for.

    The inner/outer relationship inverts between the two providers, deliberately
    in both cases:

    * Bedrock — botocore's read_timeout is 180s and this is 165s, so a hung call
      is abandoned out here while the request itself runs on and bills. That is
      why _converse_sync owns the usage sink.
    * OpenRouter — OPENROUTER_TEXT_TIMEOUT is the SHORTER of the two, so a slow
      call comes back as an ordinary exception we can attribute to a model and
      retry, and this budget is only a backstop against a wedged socket.
    """
    if TEXT_PROVIDER == "openrouter":
        return OPENROUTER_TEXT_TIMEOUT + 15.0
    return 165.0


async def chat_complete(
    system_message: str,
    user_text: str,
    provider_chain: list[str] | None = None,
) -> str:
    """Run chat completion with retries-per-model then fall through.

    Pass `provider_chain` to override the default model chain (e.g. for
    competitive-exam-specific routing). For each model we retry up to
    RETRY_MAX_ATTEMPTS times on TRANSIENT upstream errors (throttling,
    5xx, timeouts) with exponential backoff. Non-transient errors (auth,
    model access, malformed request) fail fast and we move to the next model.

    Which provider serves the chain is set by TEXT_PROVIDER; nothing in this
    function depends on it beyond _text_complete_sync and _text_timeout.

    IMPORTANT: both provider clients are synchronous and a call can block for
    60-150s. Awaiting one on the event loop would freeze the whole FastAPI app —
    every other request would queue behind it. Each call therefore runs in a
    worker thread so the main loop stays free.
    """
    if LOAD_TEST_STUB:
        # Deliberately blocking sleep dispatched through asyncio.to_thread, NOT
        # asyncio.sleep — the real path above is asyncio.to_thread(_converse_sync),
        # which consumes a slot in asyncio's default ThreadPoolExecutor
        # (min(32, cpu_count + 4)). That pool is a genuine production ceiling on
        # concurrent LLM calls, and an asyncio.sleep stub would hide it entirely.
        await asyncio.to_thread(time.sleep, STUB_LATENCY_MS / 1000.0)
        return _stub_response(system_message)

    PER_CALL_TIMEOUT = _text_timeout()
    last_err: Exception | None = None
    chain = provider_chain or PROVIDER_CHAIN

    for model_id in chain:
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                # Retries and fallback models are independently billable.
                await reserve_persisted_provider_attempt()
                budget = current_review_budget()
                timeout = min(PER_CALL_TIMEOUT, budget.remaining_seconds()) if budget else PER_CALL_TIMEOUT
                if timeout <= 0:
                    # reserve_provider_attempt normally catches this first;
                    # retain the guard for the tiny race between reservation
                    # and constructing the awaited request.
                    raise TimeoutError("The final-quality budget expired before the provider request started")
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        _text_complete_sync, model_id, system_message, user_text
                    ),
                    timeout=timeout,
                )
                if response:
                    if attempt > 1:
                        logger.info(
                            f"LLM success with {model_id} on attempt {attempt}"
                        )
                    else:
                        logger.info(f"LLM success with {model_id}")
                    return response
                last_err = RuntimeError(f"{model_id} returned an empty response")
            except asyncio.TimeoutError:
                last_err = TimeoutError(
                    f"LLM call to {model_id} exceeded {PER_CALL_TIMEOUT:.0f}s"
                )
                if attempt < RETRY_MAX_ATTEMPTS:
                    delay = RETRY_BASE_DELAY_SEC * (RETRY_BACKOFF ** (attempt - 1))
                    logger.warning(
                        f"LLM {model_id} attempt {attempt}/{RETRY_MAX_ATTEMPTS} "
                        f"timed out, retry in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    f"LLM model {model_id} timed out (attempt {attempt})"
                )
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                transient = _is_transient(e)
                if transient and attempt < RETRY_MAX_ATTEMPTS:
                    delay = RETRY_BASE_DELAY_SEC * (RETRY_BACKOFF ** (attempt - 1))
                    logger.warning(
                        f"LLM {model_id} attempt {attempt}/{RETRY_MAX_ATTEMPTS} "
                        f"transient error, retry in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    f"LLM model {model_id} failed (attempt {attempt}, "
                    f"transient={transient}): {e}"
                )
                break
    raise RuntimeError(f"All LLM providers failed: {last_err}")


_hf_lock = threading.Lock()
_hf_session_obj = None

# Set on the first successful call, cleared on a 5xx. Governs which timeout
# budget the next call gets: a genuinely cold endpoint is worth waiting minutes
# for, but once we know it is up, a hang should surface quickly.
_hf_warm = False

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _hf_session():
    """Lazily create (and cache) the Hugging Face endpoint session.

    Same lazy double-checked-lock shape as _image_client(), for the same
    reason: importing this module must never require credentials.
    """
    global _hf_session_obj
    if _hf_session_obj is None:
        with _hf_lock:
            if _hf_session_obj is None:
                import requests

                s = requests.Session()
                s.headers.update(
                    {
                        "Authorization": f"Bearer {HF_API_TOKEN}",
                        "Content-Type": "application/json",
                        # Ask for PNG; _normalize_png handles endpoints that
                        # ignore this and return JPEG anyway.
                        "Accept": "image/png",
                        # Honoured by some HF containers — holds the connection
                        # while the model loads instead of 503-ing immediately.
                        # Harmless where it is ignored; we poll regardless.
                        "x-wait-for-model": "true",
                    }
                )
                _hf_session_obj = s
    return _hf_session_obj


def _normalize_png(data: bytes) -> bytes:
    """Return PNG bytes, converting if the endpoint sent something else.

    workers.py stores diagrams under a .png key with an image/png content type,
    and pdf_utils/PIL reads them back. Normalising here keeps that whole
    downstream contract unchanged no matter what the endpoint's container
    happens to emit.
    """
    if data.startswith(_PNG_MAGIC):
        return data
    import io

    from PIL import Image  # lazy: keeps module import light

    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(buf, format="PNG")
    logger.info("Converted diagram from non-PNG endpoint response to PNG")
    return buf.getvalue()


def _generate_image_hf(prompt: str) -> bytes | None:
    """Blocking Hugging Face text-to-image call. Always run in a worker thread.

    Unlike Bedrock, a success is raw image bytes in the body rather than base64
    inside JSON, so _parse_image_response() plays no part here.
    """
    global _hf_warm

    if not HF_ENDPOINT_URL:
        raise RuntimeError("IMAGE_PROVIDER=huggingface but HF_ENDPOINT_URL is unset")
    if not HF_API_TOKEN:
        raise RuntimeError("IMAGE_PROVIDER=huggingface but HF_API_TOKEN is unset")

    payload = {
        "inputs": prompt,
        "parameters": {
            "negative_prompt": _NEGATIVE_PROMPT,
            "width": HF_IMAGE_SIZE,
            "height": HF_IMAGE_SIZE,
            "num_inference_steps": HF_IMAGE_STEPS,
            "guidance_scale": HF_IMAGE_GUIDANCE,
        },
    }

    budget = HF_READ_TIMEOUT if _hf_warm else HF_COLD_START_TIMEOUT
    started = time.perf_counter()
    deadline = started + budget
    delay = 2.0
    attempt = 0

    while True:
        attempt += 1
        if attempt > 1:
            reserve_thread_provider_retry()
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError(
                f"HF endpoint did not respond within {budget:.0f}s "
                f"({attempt - 1} attempt(s))"
            )
        resp = _hf_session().post(
            HF_ENDPOINT_URL,
            json=payload,
            # Cap the per-request read at what's left of the budget so a single
            # hung socket cannot outlive the deadline.
            timeout=(10, max(5.0, min(remaining, HF_READ_TIMEOUT))),
        )

        if resp.status_code == 200:
            break

        if resp.status_code in (401, 403):
            # A bad token will never come good — fail now rather than retrying
            # for five minutes. Note the body, never the request headers.
            raise RuntimeError(
                f"HF endpoint rejected credentials ({resp.status_code}). "
                "Check HF_API_TOKEN."
            )

        if resp.status_code in (502, 503, 504):
            # Endpoint initialising — the scale-to-zero wake-up path.
            _hf_warm = False
            if attempt == 1:
                logger.info(
                    f"HF endpoint cold, waiting up to {budget:.0f}s for it to load"
                )
            time.sleep(min(delay, max(0.0, deadline - time.perf_counter())))
            delay = min(delay * 1.5, 15.0)
            continue

        raise RuntimeError(
            f"HF endpoint returned {resp.status_code}: {resp.text[:300]}"
        )

    _hf_warm = True
    latency_ms = int((time.perf_counter() - started) * 1000)

    ctype = (resp.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        # Some containers report an error with a 200. Treat it as "no image",
        # which generate_diagram() already handles.
        logger.warning(f"HF endpoint returned JSON, not an image: {resp.text[:300]}")
        return None

    data = resp.content
    if not data:
        return None

    if _image_usage_sink:
        # Billed per GPU-hour rather than per image, so this counts throughput
        # rather than spend — still the number worth watching under load.
        try:
            _image_usage_sink(HF_IMAGE_MODEL, images=1, latency_ms=latency_ms)
        except Exception:  # noqa: BLE001 — accounting must never break generation
            logger.debug("image usage sink failed", exc_info=True)

    return _normalize_png(data)


_openrouter_lock = threading.Lock()
_openrouter_session_obj = None

_vertex_lock = threading.Lock()
_vertex_client_obj = None


def _vertex_client():
    """Lazily create the ADC-backed Gemini API on Vertex AI client."""
    global _vertex_client_obj
    if not VERTEX_PROJECT_ID:
        raise RuntimeError("IMAGE_PROVIDER=vertex but GCP_PROJECT_ID is unset")
    if _vertex_client_obj is None:
        with _vertex_lock:
            if _vertex_client_obj is None:
                from google import genai

                _vertex_client_obj = genai.Client(
                    vertexai=True,
                    project=VERTEX_PROJECT_ID,
                    location=VERTEX_IMAGE_LOCATION,
                )
    return _vertex_client_obj


def _vertex_status_code(error: BaseException) -> int | None:
    """Extract an HTTP status from either google-genai or api-core errors."""
    for attr in ("status_code", "code"):
        value = getattr(error, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:  # noqa: BLE001 - best-effort error classification
                continue
        if isinstance(value, int):
            return value
        raw = getattr(value, "value", value)
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    match = re.search(r"(?:status|code)\s*[=:]?\s*(\d{3})", str(error), re.I)
    return int(match.group(1)) if match else None


def _vertex_image_parts(response) -> list:
    """Return image parts regardless of whether Gemini also returned text."""
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        images = [part for part in parts if getattr(part, "inline_data", None)]
        if images:
            return images
    return []


def _generate_image_vertex(prompt: str) -> bytes | None:
    """Generate one Gemini image through Vertex AI using ADC.

    Gemini image models require both TEXT and IMAGE response modalities. Text
    parts are deliberately ignored; a diagram is successful only when an inline
    image part is returned. OpenRouter is not consulted if this path fails.
    """
    from google.genai import types

    started = time.perf_counter()
    deadline = started + VERTEX_TIMEOUT
    delay = 2.0
    attempt = 0
    while True:
        attempt += 1
        if attempt > 1:
            reserve_thread_provider_retry()
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError(
                f"Vertex AI did not respond within {VERTEX_TIMEOUT:.0f}s "
                f"({attempt - 1} attempt(s))"
            )
        try:
            config = types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                candidate_count=1,
                image_config=types.ImageConfig(aspect_ratio=VERTEX_IMAGE_ASPECT_RATIO),
                # The SDK expects milliseconds. Rebuild per attempt so one
                # socket cannot outlive the provider-wide retry deadline.
                http_options=types.HttpOptions(
                    timeout=int(max(5.0, remaining) * 1000)
                ),
            )
            response = _vertex_client().models.generate_content(
                model=VERTEX_IMAGE_MODEL,
                contents=prompt,
                config=config,
            )
            break
        except Exception as error:  # noqa: BLE001 - provider exceptions vary by SDK version
            status = _vertex_status_code(error)
            if status in (401, 403):
                raise RuntimeError(
                    f"Vertex AI rejected credentials or model access ({status}). "
                    "Check the VM service account's roles/aiplatform.user grant."
                ) from error
            if status not in (408, 409, 429, 500, 502, 503, 504) or attempt > VERTEX_MAX_RETRIES:
                raise RuntimeError(
                    f"Vertex AI image generation failed"
                    f"{f' ({status})' if status else ''}: {str(error)[:300]}"
                ) from error
            wait = min(delay, max(0.0, deadline - time.perf_counter()))
            if status == 429:
                logger.warning(
                    "Vertex AI rate-limited (429), retrying in %.1fs "
                    "(attempt %s/%s); consider lowering DIAGRAM_MAX_CONCURRENCY "
                    "(currently %s)",
                    wait,
                    attempt,
                    VERTEX_MAX_RETRIES,
                    DIAGRAM_MAX_CONCURRENCY,
                )
            time.sleep(wait)
            delay = min(delay * 1.5, 15.0)

    image_parts = _vertex_image_parts(response)
    if not image_parts:
        logger.warning("Vertex AI returned no image part")
        return None
    data = getattr(image_parts[0].inline_data, "data", None)
    if not data:
        logger.warning("Vertex AI returned an empty image part")
        return None

    latency_ms = int((time.perf_counter() - started) * 1000)
    if _image_usage_sink:
        try:
            usage = getattr(response, "usage_metadata", None)
            output_tokens = int(
                getattr(usage, "candidates_token_count", 0)
                or getattr(usage, "output_token_count", 0)
                or 0
            )
            total_tokens = int(getattr(usage, "total_token_count", 0) or output_tokens)
            _image_usage_sink(
                VERTEX_IMAGE_MODEL,
                images=1,
                latency_ms=latency_ms,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )
        except Exception:  # noqa: BLE001 - accounting must never break generation
            logger.debug("image usage sink failed", exc_info=True)
    return _normalize_png(bytes(data))


def _openrouter_session():
    """Lazily create (and cache) the OpenRouter session.

    Same lazy double-checked-lock shape as _hf_session(), for the same reason:
    importing this module must never require credentials.
    """
    global _openrouter_session_obj
    if _openrouter_session_obj is None:
        with _openrouter_lock:
            if _openrouter_session_obj is None:
                import requests

                s = requests.Session()
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                }
                # Attribution only; OpenRouter ignores them when absent.
                if OPENROUTER_REFERER:
                    headers["HTTP-Referer"] = OPENROUTER_REFERER
                if OPENROUTER_TITLE:
                    headers["X-Title"] = OPENROUTER_TITLE
                s.headers.update(headers)
                _openrouter_session_obj = s
    return _openrouter_session_obj


def _retry_after_seconds(resp, default: float) -> float:
    """Seconds to wait before retrying, preferring the server's own advice.

    Retry-After is only sent when the upstream provider supplies one, so the
    caller's backoff is the fallback rather than the exception.
    """
    raw = (resp.headers.get("Retry-After") or "").strip()
    if raw:
        try:
            # Always the delta-seconds form in practice; an HTTP-date would
            # raise here and fall through to the caller's backoff.
            return max(0.0, float(raw))
        except ValueError:
            pass
    return default


def _generate_image_openrouter(prompt: str) -> bytes | None:
    """Blocking OpenRouter text-to-image call. Always run in a worker thread.

    Uses the dedicated /images endpoint rather than chat completions, so the
    response is `data[0].b64_json` — raw base64, not a data: URL — alongside a
    `usage` block. Note that unlike the Bedrock path this is billed per image
    OUTPUT TOKEN (~1290 per 1024x1024 image), which is why the token counts are
    forwarded to the usage sink rather than just an image count.

    Deliberately does NOT pin provider.only: the Gemini image models are served
    by both Google AI Studio and Google Vertex at the same price, and letting
    OpenRouter fall back between them is free burst headroom.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("IMAGE_PROVIDER=openrouter but OPENROUTER_API_KEY is unset")

    payload = {
        "model": OPENROUTER_IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "aspect_ratio": OPENROUTER_ASPECT_RATIO,
    }

    started = time.perf_counter()
    deadline = started + OPENROUTER_TIMEOUT
    delay = 2.0
    attempt = 0

    while True:
        attempt += 1
        if attempt > 1:
            reserve_thread_provider_retry()
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError(
                f"OpenRouter did not respond within {OPENROUTER_TIMEOUT:.0f}s "
                f"({attempt - 1} attempt(s))"
            )
        resp = _openrouter_session().post(
            f"{OPENROUTER_BASE_URL}/images",
            json=payload,
            # Cap the per-request read at what's left of the budget so a single
            # hung socket cannot outlive the deadline.
            timeout=(10, max(5.0, remaining)),
        )

        if resp.status_code == 200:
            break

        if resp.status_code in (401, 403):
            # A bad key will never come good — fail now rather than retrying
            # for the full budget. Note the body, never the request headers.
            raise RuntimeError(
                f"OpenRouter rejected credentials ({resp.status_code}). "
                "Check OPENROUTER_API_KEY."
            )

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt > OPENROUTER_MAX_RETRIES:
                raise RuntimeError(
                    f"OpenRouter still returning {resp.status_code} after "
                    f"{OPENROUTER_MAX_RETRIES} retries: {resp.text[:300]}"
                )
            wait = _retry_after_seconds(resp, delay)
            if resp.status_code == 429:
                # Distinct from a model failure on purpose: a throttled run
                # needs DIAGRAM_MAX_CONCURRENCY lowered, not a prompt change.
                logger.warning(
                    f"OpenRouter rate-limited (429), retrying in {wait:.1f}s "
                    f"(attempt {attempt}/{OPENROUTER_MAX_RETRIES}); consider "
                    f"lowering DIAGRAM_MAX_CONCURRENCY (currently "
                    f"{DIAGRAM_MAX_CONCURRENCY})"
                )
            time.sleep(min(wait, max(0.0, deadline - time.perf_counter())))
            delay = min(delay * 1.5, 15.0)
            continue

        raise RuntimeError(f"OpenRouter returned {resp.status_code}: {resp.text[:300]}")

    latency_ms = int((time.perf_counter() - started) * 1000)

    try:
        body = resp.json()
    except ValueError:
        logger.warning(f"OpenRouter returned a non-JSON body: {resp.text[:300]}")
        return None

    items = body.get("data") or []
    if not items or not items[0].get("b64_json"):
        # Content filters and empty generations land here. Treat it the same as
        # "no image", which generate_diagram() already handles.
        logger.warning(f"OpenRouter returned no image: {str(body)[:300]}")
        return None

    if _image_usage_sink:
        # Billed per output token, so the token counts are the number that maps
        # to spend; images=1 keeps the diagram count comparable across providers.
        try:
            usage = body.get("usage") or {}
            _image_usage_sink(
                OPENROUTER_IMAGE_MODEL,
                images=1,
                latency_ms=latency_ms,
                output_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            )
        except Exception:  # noqa: BLE001 — accounting must never break generation
            logger.debug("image usage sink failed", exc_info=True)

    return _normalize_png(base64.b64decode(items[0]["b64_json"]))


def _parse_image_response(payload: dict) -> bytes | None:
    """Decode a Stability invoke_model payload to PNG bytes, or None.

    Stability signals a content-filter block with a non-null finish_reasons
    entry and still returns HTTP 200 — there is no `error` field to raise on.
    Treat it the same as "no image", which generate_diagram() already handles.
    """
    reasons = payload.get("finish_reasons") or []
    if reasons and reasons[0]:
        logger.warning(f"Diagram rejected by the image model: {reasons[0]}")
        return None
    images = payload.get("images") or []
    if not images:
        return None
    return base64.b64decode(images[0])


def _generate_image_bedrock(prompt: str) -> bytes | None:
    """Blocking Stability-on-Bedrock text-to-image call. Run in a worker thread.

    The Stability models take an aspect_ratio rather than width/height; "1:1"
    gives the same square figure Nova Canvas produced at 1024x1024, which is
    what pdf_utils._make_diagram_image() expects to fit into 100x90mm. Note
    that the resolution varies by model — sd3-5-large returns 1024x1024, while
    stable-image-core returns 1536x1536 for the same "1:1".
    """
    body = {
        "prompt": prompt,
        "mode": "text-to-image",
        "aspect_ratio": "1:1",
        "output_format": "png",
        "negative_prompt": _NEGATIVE_PROMPT,
    }
    started = time.perf_counter()
    resp = _image_client().invoke_model(
        modelId=DIAGRAM_MODEL,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    if _image_usage_sink:
        # Billed per image, not per token — and billed even when the content
        # filter rejects the result, so this is recorded before parsing.
        # invoke_model has no metrics block of its own, hence the manual timing.
        try:
            _image_usage_sink(
                DIAGRAM_MODEL,
                images=len(payload.get("images") or []),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception:  # noqa: BLE001 — accounting must never break generation
            logger.debug("image usage sink failed", exc_info=True)
    return _parse_image_response(payload)


def _generate_image_sync(prompt: str) -> bytes | None:
    """Dispatch to the configured image provider. Always run in a worker thread."""
    if IMAGE_PROVIDER == "huggingface":
        return _generate_image_hf(prompt)
    if IMAGE_PROVIDER == "vertex":
        return _generate_image_vertex(prompt)
    if IMAGE_PROVIDER == "openrouter":
        return _generate_image_openrouter(prompt)
    return _generate_image_bedrock(prompt)


def _diagram_timeout() -> float:
    """Outer budget for one diagram, matched to the provider.

    This wraps the whole worker-thread call, so it has to exceed whatever the
    inner retry loop might spend. Getting this wrong is silent: generate_diagram
    swallows the TimeoutError and the question simply loses its figure.
    """
    if IMAGE_PROVIDER == "vertex":
        return VERTEX_TIMEOUT + 30.0
    if IMAGE_PROVIDER == "openrouter":
        return OPENROUTER_TIMEOUT + 30.0
    if IMAGE_PROVIDER != "huggingface":
        return 75.0
    inner = HF_READ_TIMEOUT if _hf_warm else HF_COLD_START_TIMEOUT
    return inner + 30.0  # headroom for connect, transfer and PNG conversion


def _diagram_prompt(spec: str | dict) -> str:
    """Build the image prompt for the configured provider.

    The two provider families want opposite things. Diffusion models weight the
    opening tokens heavily and largely ignore long instruction-style prompts —
    the original Nova Canvas wording came back as a colour 3D render despite
    asking for black-and-white line art in the first sentence — so they get a
    keyword-led prompt. Gemini follows instructions and can spell, so it gets
    prose, and asks for the labels the diffusion prompt has to forbid.

    `spec` is the structured figure spec from diagram_policy (already vetted and
    scrubbed) or, for papers generated before that existed, a plain description
    string. The structured form is what makes the annotation whitelist below
    possible: the earlier version of this prompt told Gemini to "label the parts
    clearly", and on a geometry question it duly printed the value the student
    was asked to find. Labels are now an explicit list, and any measurement not
    in `given_values` is forbidden outright.
    """
    if isinstance(spec, dict):
        description = spec.get("figure") or ""
        labels = [str(x) for x in (spec.get("labels") or [])]
        givens = [str(x) for x in (spec.get("given_values") or [])]
        unknowns = [str(x) for x in (spec.get("unknowns") or [])]
    else:
        description, labels, givens, unknowns = str(spec), [], [], []

    if IMAGE_PROVIDER in ("openrouter", "vertex"):
        # The exclusions carried by _NEGATIVE_PROMPT have to live in the
        # positive text here: the Gemini image endpoints take no negative
        # prompt. The blanket label ban is still dropped — legible labelling is
        # the reason to use this provider — but it is now a closed list rather
        # than an invitation to annotate whatever the model thinks it sees.
        parts = [
            f"A black and white line drawing for a school exam paper: "
            f"{description}. "
            f"Draw it as a clean, simple, flat 2D diagram with plain black "
            f"outlines on a plain white background, in the style of a "
            f"scientific textbook figure."
        ]
        if labels:
            parts.append(
                "Print these labels and no others, spelled exactly as given, "
                "with short leader lines where needed: " + ", ".join(labels) + "."
            )
        if givens:
            parts.append(
                "Print these measurements exactly as written and no others: "
                + "; ".join(givens)
                + "."
            )
        if unknowns:
            parts.append(
                "Mark each of the following with its symbol alone — never with "
                "a number, since it is what the student must work out: "
                + "; ".join(unknowns)
                + "."
            )
        parts.append(
            "Do not print any measurement, angle, length, coordinate, value or "
            "annotation that is not listed above. Do not show any working, "
            "solution steps, construction lines or arrows indicating a "
            "calculation."
        )
        parts.append(
            "No colour, no shading or gradients, no 3D or perspective, no "
            "photorealism or texture, no watermark, no title, no border."
        )
        return " ".join(parts)

    # Diffusion path: text is suppressed wholesale (see _NEGATIVE_PROMPT), so
    # the givens can only be conveyed by the question text, not the figure.
    return (
        f"black and white line art diagram, {description}, "
        f"simple clean outlines, monochrome, flat 2D, white background, "
        f"scientific textbook illustration, unlabelled"
    )


# Guards the process-wide diagram concurrency (see DIAGRAM_MAX_CONCURRENCY).
# Created on first use rather than at import because a Semaphore binds to the
# running loop, and importing this module must not require one. Keyed by loop so
# tests that spin up a fresh event loop per case don't inherit a stale binding.
_diagram_sem_loop = None
_diagram_sem = None


def _diagram_semaphore() -> asyncio.Semaphore:
    global _diagram_sem, _diagram_sem_loop
    loop = asyncio.get_running_loop()
    if _diagram_sem is None or _diagram_sem_loop is not loop:
        _diagram_sem = asyncio.Semaphore(DIAGRAM_MAX_CONCURRENCY)
        _diagram_sem_loop = loop
    return _diagram_sem


async def generate_diagram(spec: str | dict) -> bytes | None:
    """Generate a clean B&W line diagram for an exam question. Returns PNG bytes
    or None on failure. Non-raising — diagrams are best-effort.

    `spec` is a vetted diagram spec (see diagram_policy.apply_diagram_policy) or
    a plain description string for papers that predate it.

    Runs in a worker thread so the blocking HTTP call doesn't stall the FastAPI
    event loop (see chat_complete for the same pattern). The provider behind it
    is IMAGE_PROVIDER; this function's contract is the same either way.

    Concurrency is bounded here rather than at the call site because the burst
    comes from several papers overlapping, which no single call site can see.
    """
    if not ENABLE_DIAGRAMS:
        return None
    if LOAD_TEST_STUB:
        # Image calls are shorter than text ones; quarter the think-time. Same
        # to_thread dispatch as the real path so it competes for the same pool.
        await asyncio.to_thread(time.sleep, STUB_LATENCY_MS / 4000.0)
        return _STUB_PNG
    try:
        # Held across the whole call, so the ceiling counts requests actually in
        # flight at the provider — waiting here is the point, not a cost.
        async with _diagram_semaphore():
            await reserve_persisted_provider_attempt()
            budget = current_review_budget()
            return await asyncio.wait_for(
                asyncio.to_thread(_generate_image_sync, _diagram_prompt(spec)),
                timeout=min(_diagram_timeout(), budget.remaining_seconds()) if budget else _diagram_timeout(),
            )
    except Exception as e:  # noqa: BLE001
        label = spec.get("figure", "") if isinstance(spec, dict) else str(spec)
        logger.warning(f"Diagram generation failed for '{label[:40]}': {e}")
        return None


def _repair_json_string_escapes(text: str) -> str:
    """Make the one safe JSON repair common in mathematical LLM output.

    LaTeX such as ``\\vec`` is valid prose but not valid JSON unless the
    backslash is escaped.  Only repair invalid escapes *inside JSON strings*;
    this never changes JSON structure or invents a missing value.
    """
    out: list[str] = []
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            out.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            out.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\":
            following = text[index + 1] if index + 1 < len(text) else ""
            unicode_escape = (
                following == "u"
                and index + 5 < len(text)
                and all(c in "0123456789abcdefABCDEF" for c in text[index + 2:index + 6])
            )
            latex_command = re.match(r'\\(?:theta|tau|times|text|frac|beta|begin|nu|nabla|rho|right)\b', text[index:])
            if (following in '"\\/bfnrt' or unicode_escape) and not latex_command:
                out.append(char)
            else:
                # Keep the visible character, but make its backslash literal.
                out.append("\\\\")
            if following == "\\":
                out.append(following)
                index += 2
            else:
                index += 1
            continue
        if char in "\r\n":
            out.append("\\n")
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _loads_json(text: str) -> Any:
    """Load JSON, retrying once after a strictly syntax-only escape repair."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as original:
        repaired = _repair_json_string_escapes(text)
        if repaired == text:
            raise original
        return json.loads(repaired)


def parse_json_response(text: str) -> Any:
    """Extract a JSON payload from an LLM response, tolerating code fences,
    prose, and partial/truncated output (will salvage a complete answers array
    from a truncated solution response)."""
    if not text:
        raise MalformedProviderResponseError("The AI provider returned an empty response.")

    # Strip code fences
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    text = text.strip()

    # Direct parse
    try:
        return _loads_json(text)
    except json.JSONDecodeError:
        pass

    # Greedy object / array match
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pattern, text)
        if m:
            try:
                return _loads_json(m.group(0))
            except json.JSONDecodeError:
                continue

    # Salvage a truncated `answers` array (common with long step-by-step solutions)
    salvage = re.search(r'"answers"\s*:\s*\[(.*)', text, re.DOTALL)
    if salvage:
        items = _walk_top_level_objects(salvage.group(1))
        if items:
            return {"answers": items}

    # Salvage a truncated paper: pull "instructions" if present + walk any
    # complete question objects out of the (possibly broken) sections array.
    # Crucially, retain the section metadata that preceded each questions
    # array.  Collapsing everything into "Recovered Questions" makes a
    # recoverable transport failure look like a blueprint violation and means
    # a later top-up has no section to merge into.
    instr_m = re.search(r'"instructions"\s*:\s*"([^"]*)"', text)
    instructions = instr_m.group(1) if instr_m else ""
    # Walk every `"questions": [` block in the response; for each, scan the
    # remaining text and collect complete question objects until brace depth
    # closes the array (or text ends).
    recovered_sections: list[dict] = []
    by_section: dict[tuple[str, str], dict] = {}
    seen: set = set()
    for qm in re.finditer(r'"questions"\s*:\s*\[', text):
        metadata = _section_metadata_before(text, qm.start())
        title = metadata.get("title") or "Recovered Questions"
        section_id = metadata.get("section_id") or ""
        key = (section_id, title)
        section = by_section.get(key)
        if section is None:
            section = {**metadata, "title": title, "questions": []}
            by_section[key] = section
            recovered_sections.append(section)
        for q in _walk_top_level_objects(text[qm.end():]):
            sig = (q.get("question", ""), tuple(q.get("options", []) or []))
            if sig in seen:
                continue
            seen.add(sig)
            section["questions"].append(q)
    if any(section["questions"] for section in recovered_sections):
        return {
            "instructions": instructions,
            "sections": [section for section in recovered_sections if section["questions"]],
            "_recovered_from_truncation": True,
        }

    # Do not include provider content in a user-visible exception. Besides
    # being noisy, raw model output can contain source material and prompts.
    raise MalformedProviderResponseError(
        "The AI provider returned an incomplete response."
    )


def _walk_top_level_objects(body: str) -> list:
    """Yield every fully-closed top-level JSON object from `body`. Used to
    salvage partial arrays from truncated LLM output."""
    items: list = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for i, ch in enumerate(body):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        # `body` starts immediately after a questions-array opening bracket.
        # Stop at that array's close rather than continuing into the next
        # section, where its whole section object could otherwise be mistaken
        # for another question.
        if ch == "]" and depth == 0:
            break
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunk = body[start : i + 1]
                try:
                    items.append(_loads_json(chunk))
                except json.JSONDecodeError:
                    pass
                start = None
    return items


def _section_metadata_before(text: str, position: int) -> dict:
    """Return title/section_id from the object owning a questions array.

    A truncated response is not valid JSON as a whole, but everything before a
    `"questions": [` key is balanced. Tracking opening braces identifies that
    section object without guessing from titles in earlier sections or question
    text. The values themselves are decoded with ``json.loads`` so escaped
    titles remain correct.
    """
    stack: list[int] = []
    in_string = False
    escape = False
    for index, ch in enumerate(text[:position]):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append(index)
        elif ch == "}" and stack:
            stack.pop()
    if not stack:
        return {}

    prefix = text[stack[-1]:position]
    result: dict = {}
    quoted = r'("(?:\\.|[^"\\])*")'
    for field in ("section_id", "title"):
        match = re.search(rf'"{field}"\s*:\s*{quoted}', prefix)
        if not match:
            continue
        try:
            result[field] = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    return result
