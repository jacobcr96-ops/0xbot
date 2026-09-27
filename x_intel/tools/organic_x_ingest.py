"""CLI: document CA-scoped X search + write ``data/x_organic/<mint>.json``.

Python has no MCP — the agent routine calls ``search_posts_all`` then either:
  - ``python -m x_intel.tools.organic_x_ingest --mint CA --from-json path``
  - or writes the JSON shape this tool prints.

Usage::

    # Print recommended MCP query + empty stub path
    python -m x_intel.tools.organic_x_ingest --mint <CA>

    # Ingest agent-fetched search_posts_all results
    python -m x_intel.tools.organic_x_ingest --mint <CA> --from-json /tmp/posts.json

    # Write empty stub (documents shape for scans)
    python -m x_intel.tools.organic_x_ingest --mint <CA> --write-stub

Agent routine (max ~5 CAs/cycle to save X credits)::

    1. Pick ≤5 early unique candidates under ~$1M (pursue/watch, not clone_storm).
    2. MCP ``search_posts_all`` with ``query=<exact CA>``, ``sort_order=recency``,
       ``max_results=10`` (optional ``start_time`` = now-6h ISO).
    3. Normalize posts → ``{id,text,author,created_at,url,metrics}``.
    4. Write via this CLI ``--from-json`` (or hand-write ``data/x_organic/<mint>.json``).
    5. Next discovery enrich/pipeline cycle stamps ``organic_x`` when ≥1 recent
       CA mention passes (rejects pump.twitter URL reuse / ticker-only spam).

Env for scans: ``XINTEL_X_SOCIAL_LIVE=1`` and
``XINTEL_ORGANIC_X_REQUIRED_FOR_PING=1`` (Jacob ping prefers organic_x).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from x_intel.config import data_dir
from x_intel.discovery.organic_x import (
    ca_search_query,
    empty_organic_payload,
    evaluate_organic_payload,
    organic_x_path,
    write_organic_payload,
)


def _normalize_post(raw: dict[str, Any]) -> dict[str, Any]:
    """Map MCP / agent post shapes → disk schema."""
    author = raw.get("author")
    if isinstance(author, dict):
        author_out: Any = (
            author.get("username")
            or author.get("name")
            or author.get("id")
            or author
        )
    else:
        author_out = author
    metrics = raw.get("metrics") or raw.get("public_metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    return {
        "id": str(raw.get("id") or raw.get("post_id") or ""),
        "text": str(raw.get("text") or raw.get("full_text") or ""),
        "author": author_out,
        "created_at": str(
            raw.get("created_at") or raw.get("createdAt") or ""
        ),
        "url": str(raw.get("url") or raw.get("post_url") or ""),
        "metrics": {
            "like_count": metrics.get("like_count") or metrics.get("likeCount"),
            "repost_count": metrics.get("repost_count")
            or metrics.get("retweet_count")
            or metrics.get("repostCount"),
            "reply_count": metrics.get("reply_count") or metrics.get("replyCount"),
            "quote_count": metrics.get("quote_count") or metrics.get("quoteCount"),
        },
    }


def _load_from_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {"posts": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"expected object or list in {path}")
    # MCP search_posts_all often nests under data / posts / results
    if "posts" in raw:
        return raw
    for key in ("data", "results", "statuses"):
        if isinstance(raw.get(key), list):
            return {**raw, "posts": raw[key]}
    return raw


def build_payload(
    mint: str,
    *,
    from_json: Optional[Path] = None,
    queried_at: Optional[str] = None,
) -> dict[str, Any]:
    now = queried_at or datetime.now(timezone.utc).isoformat()
    if from_json is None:
        return empty_organic_payload(mint, queried_at=now)
    loaded = _load_from_json(from_json)
    posts_in = loaded.get("posts") or []
    posts = [_normalize_post(p) for p in posts_in if isinstance(p, dict)]
    return {
        "query": str(loaded.get("query") or ca_search_query(mint)),
        "queried_at": str(loaded.get("queried_at") or now),
        "posts": posts,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Organic X ingest helper (agent writes CA-scoped search JSON)"
    )
    parser.add_argument("--mint", required=True, help="Contract address / mint CA")
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="Agent-fetched search_posts_all JSON (list or {posts:[...]})",
    )
    parser.add_argument(
        "--write-stub",
        action="store_true",
        help="Write empty JSON shape even without --from-json",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override XINTEL_DATA_DIR (default: repo data/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payload / eval only; do not write",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    mint = args.mint.strip()
    root = args.data_dir or data_dir()
    query = ca_search_query(mint)
    out_path = organic_x_path(mint, data_root=root)

    print("=== organic_x ingest (agent MCP routine) ===")
    print(f"mint:              {mint}")
    print(f"search_posts_all:  query={query!r}  (exact CA, not ticker)")
    print("suggested args:    sort_order=recency  max_results=10")
    print("                   start_time=<now-6h ISO>  (optional)")
    print(f"write path:        {out_path}")
    print("credit budget:     max ~5 CAs / discovery cycle")
    print("env:               XINTEL_X_SOCIAL_LIVE=1")
    print("                   XINTEL_ORGANIC_X_REQUIRED_FOR_PING=1")

    if args.from_json is None and not args.write_stub and not args.dry_run:
        print(
            "\nNo --from-json / --write-stub — printed query only. "
            "Pass --write-stub for empty shape or --from-json after MCP fetch."
        )
        return 0

    payload = build_payload(mint, from_json=args.from_json)
    eval_result = evaluate_organic_payload(mint, payload)

    if args.verbose or args.dry_run:
        print("\n--- payload ---")
        print(json.dumps(payload, indent=2)[:4000])
        print("\n--- eval ---")
        print(json.dumps({k: v for k, v in eval_result.items() if k != "matching_posts"}, indent=2))

    if args.dry_run:
        print("dry-run: not written")
        return 0

    if args.from_json is None and not args.write_stub:
        return 0

    path = write_organic_payload(mint, payload, data_root=root)
    print(f"\nwrote {path} posts={len(payload.get('posts') or [])} "
          f"organic_x={eval_result.get('organic_x')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
