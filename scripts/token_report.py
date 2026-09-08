"""Report LLM token consumption from the CSV that backend/token_usage.py writes.

Reads the CSV only — no database, no AWS, no backend imports. Safe to run while
the server is up.

    python scripts/token_report.py                  # totals by kind and model
    python scripts/token_report.py --by-paper       # one row per paper
    python scripts/token_report.py --paper <id>     # every call for one paper
    python scripts/token_report.py --since 2026-08-01
    python scripts/token_report.py --by-paper --csv > papers.csv

Exit code is 1 if the CSV is missing or no rows match the filters.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "backend" / ".env")

DEFAULT_CSV = ROOT / "backend" / "logs" / "llm_usage.csv"

# Columns summed in every rollup. Kept in one place so a schema addition only
# has to be listed once.
NUMERIC = ("input_tokens", "output_tokens", "total_tokens", "images")


def _load(path: Path, since: str | None, paper: str | None) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    for r in rows:
        for col in NUMERIC:
            try:
                r[col] = int(r.get(col) or 0)
            except ValueError:
                r[col] = 0
        try:
            r["latency_ms"] = int(r.get("latency_ms") or 0)
        except ValueError:
            r["latency_ms"] = 0

    if since:
        # Timestamps are ISO strings, so a lexical compare is a date compare.
        rows = [r for r in rows if r.get("timestamp", "") >= since]
    if paper:
        rows = [r for r in rows if r.get("ref") == paper]
    return rows


def _fmt(n: int) -> str:
    return f"{n:,}"


def _table(title: str, groups: dict[str, list[dict]], key_header: str) -> None:
    print(f"\n{title}")
    print(f"  {key_header:<26} {'calls':>7} {'input':>12} {'output':>12} {'total':>12} {'imgs':>6}")
    print(f"  {'-' * 26} {'-' * 7} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 6}")
    for key in sorted(groups, key=lambda k: -sum(r["total_tokens"] for r in groups[k])):
        rs = groups[key]
        print(
            f"  {key[:26]:<26} {len(rs):>7} "
            f"{_fmt(sum(r['input_tokens'] for r in rs)):>12} "
            f"{_fmt(sum(r['output_tokens'] for r in rs)):>12} "
            f"{_fmt(sum(r['total_tokens'] for r in rs)):>12} "
            f"{sum(r['images'] for r in rs):>6}"
        )


def _group(rows: list[dict], field: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r.get(field) or "(none)"].append(r)
    return out


def _emit_csv(rows: list[dict]) -> None:
    if not rows:
        return
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--by-paper", action="store_true", help="one row per paper/ref")
    ap.add_argument("--paper", metavar="ID", help="show every call for one paper")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="only rows on or after this date")
    ap.add_argument("--csv", action="store_true", help="emit matching rows as CSV to stdout")
    ap.add_argument(
        "--file",
        default=os.environ.get("TOKEN_USAGE_CSV") or str(DEFAULT_CSV),
        help="path to the usage CSV",
    )
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"No usage file at {path}", file=sys.stderr)
        print(
            "Generate a paper with the server running, or set TOKEN_USAGE_CSV.",
            file=sys.stderr,
        )
        return 1

    rows = _load(path, args.since, args.paper)
    if not rows:
        print("No rows match those filters.", file=sys.stderr)
        return 1

    if args.csv:
        _emit_csv(rows)
        return 0

    if args.paper:
        print(f"\nCalls for {args.paper}  ({len(rows)} total)")
        print(
            f"  {'when':<19} {'kind':<18} {'model':<32} "
            f"{'input':>10} {'output':>10} {'sec':>6}  outcome"
        )
        for r in rows:
            # Trim to seconds — the full ISO string with its UTC offset would
            # otherwise be cut mid-offset by the column width.
            when = r.get("timestamp", "")[:19].replace("T", " ")
            print(
                f"  {when:<19} {r.get('kind', ''):<18} "
                f"{r.get('model_id', '')[:32]:<32} "
                f"{_fmt(r['input_tokens']):>10} {_fmt(r['output_tokens']):>10} "
                f"{r['latency_ms'] / 1000:>6.1f}  {r.get('outcome', '')}"
            )
    elif args.by_paper:
        # Non-paper work (topic extraction, imports) shares the file but has no
        # paper to attribute to. Drop it from `rows` rather than just from the
        # table, so the totals below match what is actually shown.
        rows = [r for r in rows if r.get("ref")]
        if not rows:
            print("No paper-attributed calls recorded yet.", file=sys.stderr)
            return 1
        papers = _group(rows, "ref")
        _table(f"Per paper ({len(papers)} papers)", papers, "paper / ref")
    else:
        _table("By kind", _group(rows, "kind"), "kind")
        _table("By model", _group(rows, "model_id"), "model")

    truncated = [r for r in rows if r.get("outcome") == "truncated"]
    print(
        f"\n  {len(rows)} calls  "
        f"{_fmt(sum(r['total_tokens'] for r in rows))} tokens  "
        f"{sum(r['images'] for r in rows)} images"
    )
    if truncated:
        # Billed in full, but the response was cut off and had to be salvaged.
        print(f"  {len(truncated)} call(s) hit BEDROCK_MAX_TOKENS and were truncated")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
