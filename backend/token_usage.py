"""Per-paper LLM token accounting. Terminal lines plus a CSV file, nothing else.

Bedrock returns `usage` on every Converse call and llm_adapter discards it. This
module catches it on the way past and answers one question: what did this paper
cost in tokens?

Design constraints, both deliberate:

* **Stdlib only, and no side effects at import.** Nothing here touches the
  filesystem, the network, or a required env var until `install()` runs.
* **llm_adapter must not import this module.** It imports nothing from the
  project today, which is the only reason the offline tests collect without AWS
  configuration. `install()` therefore *pushes* callbacks into llm_adapter
  rather than llm_adapter pulling them from here. Left uninstalled, the sinks
  stay None and generation behaves byte-identically.

Nothing in here writes to Mongo or touches an API response. The paper documents
are unchanged.
"""
import contextvars
import csv
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bodhiai.tokens")

# Written on creation only; a pre-existing file is appended to as-is.
COLUMNS = [
    "timestamp",
    "kind",
    "ref",
    "model_id",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "images",
    "stop_reason",
    "latency_ms",
    "outcome",
]

_BACKEND_DIR = Path(__file__).resolve().parent
_DEFAULT_CSV = _BACKEND_DIR / "logs" / "llm_usage.csv"

# Resolved lazily in install() rather than at import, so importing this module
# never depends on the environment being loaded yet.
_csv_path: Path | None = None
_enabled = False

# One writer at a time. THREAD_POOL_MAX_WORKERS defaults to 64 and the
# competitive path fans out three concurrent batches, so several worker threads
# can land here at once.
_write_lock = threading.Lock()

# (kind, ref) for whatever work is currently in flight. asyncio.to_thread runs
# its target through contextvars.copy_context(), so a value set in the calling
# coroutine is readable inside _converse_sync on the pool thread. create_task
# and gather each copy the context at creation, so sibling batches all inherit
# the parent's tag — which is why tagging the parent covers the fan-out.
_current: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar(
    "llm_usage_tag", default=("unattributed", "")
)


def tag(kind: str, ref: str = "") -> None:
    """Label every Bedrock call made from here on in this task or request.

    Deliberately no reset token and no context manager: every caller is either a
    detached create_task or a per-request handler, so the context dies with it
    and there is nothing to unwind. That keeps each call site to a single line
    and avoids re-indenting any existing function.
    """
    _current.set((kind, ref))


def current_tag() -> tuple[str, str]:
    return _current.get()


def _write_row(row: dict) -> None:
    """Append one row. Reopens per write so a crash can't lose buffered rows."""
    if not _enabled or _csv_path is None:
        return
    with _write_lock:
        new = not _csv_path.exists()
        with _csv_path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            if new:
                w.writeheader()
            w.writerow(row)


def _record(
    kind: str,
    ref: str,
    model_id: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    images: int = 0,
    stop_reason: str = "",
    latency_ms: int = 0,
    outcome: str = "ok",
    reported_cost_usd: float | None = None,
) -> None:
    """Record one billed Bedrock call. Never raises — see the callers."""
    try:
        from usage_ledger import record_call
        record_call(dict(kind=kind, ref=ref, model_id=model_id, input_tokens=input_tokens,
                         output_tokens=output_tokens, total_tokens=total_tokens, images=images,
                         stop_reason=stop_reason, latency_ms=latency_ms, outcome=outcome, reported_cost_usd=reported_cost_usd))
    except Exception:
        logger.exception('Durable usage accounting failed')
    _write_row(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "ref": ref,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "images": images,
            "stop_reason": stop_reason,
            "latency_ms": latency_ms,
            "outcome": outcome,
        }
    )


def _short(ref: str) -> str:
    """Trim a uuid for the terminal line; the CSV keeps it whole."""
    return f"{ref[:8]}…" if len(ref) > 9 else ref


def record_tokens(
    model_id: str,
    usage: dict,
    *,
    stop_reason: str = "",
    latency_ms: int = 0,
) -> None:
    """Sink for Converse calls. Registered into llm_adapter by install()."""
    kind, ref = _current.get()
    inp = int(usage.get("inputTokens") or 0)
    out = int(usage.get("outputTokens") or 0)
    # Bedrock sends totalTokens, but derive it when absent so the column is
    # never blank and never disagrees with the two it is meant to sum.
    total = int(usage.get("totalTokens") or (inp + out))

    _record(
        kind,
        ref,
        model_id,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
        reported_cost_usd=usage.get('cost_usd'),
        stop_reason=stop_reason,
        latency_ms=latency_ms,
        # max_tokens means the response came back truncated and parse_json_response
        # had to salvage it — worth flagging in the report, since the call was
        # billed in full either way.
        outcome="failed" if usage.get('failed') else ("truncated" if stop_reason == "max_tokens" else "ok"),
    )
    logger.info(
        f"[tokens] {kind} {_short(ref)}  {model_id}  "
        f"in={inp} out={out} total={total}  {latency_ms / 1000:.1f}s"
        + ("  TRUNCATED" if stop_reason == "max_tokens" else "")
    )


def record_image(
    model_id: str,
    *,
    images: int = 1,
    latency_ms: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """Sink for diagram calls. Registered into llm_adapter by install().

    Overrides the kind to "diagram" while keeping the inherited ref, so the
    diagram flow needs no tag() of its own. generate_diagrams_for_paper is
    awaited from inside generate_paper_background, so a tag() there would
    overwrite the parent's "paper" tag for the remainder of that coroutine.

    Stability bills per image and Hugging Face per GPU-hour, so both leave the
    token counts at zero. OpenRouter bills per image OUTPUT TOKEN, and those
    reuse the existing token columns — deliberately, rather than adding a cost
    column, since COLUMNS is written at file creation only and a new column
    would desync every pre-existing CSV.
    """
    _, ref = _current.get()
    _record(
        "diagram",
        ref,
        model_id,
        images=images,
        latency_ms=latency_ms,
        output_tokens=output_tokens,
        total_tokens=total_tokens or output_tokens,
    )
    logger.info(
        f"[tokens] diagram {_short(ref)}  {model_id}  "
        f"images={images}  {latency_ms / 1000:.1f}s"
        + (f"  out={output_tokens}" if output_tokens else "")
    )


def install() -> str | None:
    """Wire the sinks into llm_adapter. Call once at startup.

    Returns the CSV path, or None when accounting is switched off. Safe to call
    more than once. Import is local so that merely importing this module never
    pulls in boto3.
    """
    global _csv_path, _enabled

    if os.environ.get("TRACK_TOKEN_USAGE", "true").lower() in ("0", "false", "no"):
        logger.info("token usage tracking disabled (TRACK_TOKEN_USAGE)")
        return None

    _csv_path = Path(os.environ.get("TOKEN_USAGE_CSV") or _DEFAULT_CSV)
    try:
        _csv_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"token usage disabled — cannot create {_csv_path.parent}: {e}")
        _csv_path = None
        return None

    _enabled = True

    import llm_adapter

    llm_adapter.set_usage_sinks(on_tokens=record_tokens, on_image=record_image)
    logger.info(f"token usage tracking -> {_csv_path}")
    return str(_csv_path)
