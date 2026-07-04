#!/usr/bin/env python3
"""Normalize Xquik social exports for sentiment collector research."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

TEXT_FIELDS = ("text", "tweet_text", "full_text", "content", "body")
SYMBOL_FIELDS = ("symbol", "ticker", "query", "keyword")


def _as_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if isinstance(payload, dict):
        for key in ("tweets", "results", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [payload]

    return []


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    source_path = Path(path)
    suffix = source_path.suffix.lower()

    if suffix == ".csv":
        with source_path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    if suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        with source_path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    rows.extend(_as_records(json.loads(stripped)))
        return rows

    if suffix == ".json":
        with source_path.open(encoding="utf-8") as handle:
            return _as_records(json.load(handle))

    raise ValueError("Use a CSV, JSON, or JSONL Xquik export.")


def row_text(row: dict[str, Any]) -> str:
    for field in TEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _explicit_symbol(row: dict[str, Any]) -> str | None:
    for field in SYMBOL_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().upper().lstrip("$")

    return None


def mentioned_symbols(row: dict[str, Any], watchlist: list[str]) -> list[str]:
    symbols = {symbol.upper().lstrip("$") for symbol in watchlist}
    matches: set[str] = set()
    explicit = _explicit_symbol(row)

    if explicit in symbols:
        matches.add(explicit)

    text = row_text(row).upper()
    for symbol in symbols:
        pattern = rf"(?<![A-Z0-9])\$?{re.escape(symbol)}(?![A-Z0-9])"
        if re.search(pattern, text):
            matches.add(symbol)

    return sorted(matches)


def mention_volume_bucket(count: int) -> str:
    if count >= 50:
        return "surging"
    if count >= 15:
        return "elevated"
    if count >= 3:
        return "normal"
    return "low"


def summarize_mentions(
    rows: list[dict[str, Any]],
    watchlist: list[str],
) -> dict[str, dict[str, Any]]:
    summary = {
        symbol.upper().lstrip("$"): {
            "mention_count": 0,
            "mention_volume": "low",
            "samples": [],
        }
        for symbol in watchlist
    }

    for row in rows:
        text = row_text(row)
        for symbol in mentioned_symbols(row, list(summary.keys())):
            entry = summary[symbol]
            entry["mention_count"] += 1
            if text and len(entry["samples"]) < 3:
                entry["samples"].append(text[:280])

    for entry in summary.values():
        entry["mention_volume"] = mention_volume_bucket(entry["mention_count"])

    return summary


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: python -m collect.xquik_social_mentions EXPORT SYMBOL [SYMBOL...]",
            file=sys.stderr,
        )
        return 2

    rows = load_rows(sys.argv[1])
    summary = summarize_mentions(rows, sys.argv[2:])
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
