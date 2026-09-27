"""Organic X quality path — CA-scoped recent posts from disk cache.

Live X MCP search stays with the agent routine (Python has no MCP). Agents
write ``data/x_organic/<mint>.json``; enrich/pipeline reads it and stamps
``organic_x`` / ``x_social`` hints so ``has_publish_quality_evidence`` can pass
without relying on pump.twitter alone.

Schema (v0)::

    {
      "query": "<exact CA>",
      "queried_at": "2026-09-20T20:00:00+00:00",
      "posts": [
        {
          "id": "...",
          "text": "...",
          "author": "handle",
          "created_at": "ISO8601",
          "url": "https://x.com/...",
          "metrics": {"like_count": 0, "repost_count": 0, ...}
        }
      ]
    }

Enable scans with ``XINTEL_X_SOCIAL_LIVE=1``. Jacob ping prefers organic_x when
``XINTEL_ORGANIC_X_REQUIRED_FOR_PING=1`` (see ``has_ping_quality_evidence``).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from x_intel.config import data_dir
from x_intel.discovery.clone_farm import non_status_spam_twitter
from x_intel.discovery.enrich import is_real_social_url
from x_intel.io_atomic import atomic_read_json, atomic_write_json

# Recent organic window for early unique candidates
ORGANIC_X_MAX_AGE_HOURS = 6.0

# Solana base58 / EVM CA mention in post text
_CA_PAT = re.compile(
    r"(?:^|[\s\"'(])((?:0x[a-fA-F0-9]{40})|(?:[1-9A-HJ-NP-Za-km-z]{32,44}))(?:$|[\s\"',).])"
)

# Status / farm paste patterns that are not organic discussion
_STATUS_URL_RE = re.compile(
    r"https?://(www\.)?(x\.com|twitter\.com)/[^/]+/status/\d+",
    re.I,
)
_AIRDROP_FARM_RE = re.compile(
    r"\bcrypto airdrop\b|\bairdrop\b.*\b(memecoin|token)\b|\bfree\s*mint\b",
    re.I,
)
# Authors that look like automated status farms (weak signal — soft prefer only)
_FARM_AUTHOR_RE = re.compile(
    r"(airdrop|claim|pumpfunbot|tokenalert|newpair|dexscreener|gmgn)",
    re.I,
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def x_social_live_enabled() -> bool:
    """True when scans should attempt CA-scoped X (agent MCP / live flag)."""
    return _env_bool("XINTEL_X_SOCIAL_LIVE", default=False)


def organic_x_required_for_ping() -> bool:
    """Jacob ping / xintel publish prefer organic_x when set."""
    return _env_bool("XINTEL_ORGANIC_X_REQUIRED_FOR_PING", default=False)


def organic_x_dir(data_root: Optional[Path] = None) -> Path:
    root = Path(data_root) if data_root else data_dir()
    return root / "x_organic"


def organic_x_path(mint: str, data_root: Optional[Path] = None) -> Path:
    mint = (mint or "").strip()
    return organic_x_dir(data_root) / f"{mint}.json"


def ca_search_query(mint: str) -> str:
    """Exact-CA query string for MCP ``search_posts_all`` (not ticker-alone)."""
    return (mint or "").strip()


def empty_organic_payload(mint: str, *, queried_at: Optional[str] = None) -> dict[str, Any]:
    """Documented JSON shape for agent ingest."""
    now = queried_at or datetime.now(timezone.utc).isoformat()
    return {
        "query": ca_search_query(mint),
        "queried_at": now,
        "posts": [],
    }


def write_organic_payload(
    mint: str,
    payload: dict[str, Any],
    *,
    data_root: Optional[Path] = None,
) -> Path:
    path = organic_x_path(mint, data_root=data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Normalize required keys
    out = {
        "query": str(payload.get("query") or ca_search_query(mint)),
        "queried_at": str(
            payload.get("queried_at") or datetime.now(timezone.utc).isoformat()
        ),
        "posts": list(payload.get("posts") or []),
    }
    atomic_write_json(path, out)
    return path


def load_organic_payload(
    mint: str, *, data_root: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    path = organic_x_path(mint, data_root=data_root)
    raw = atomic_read_json(path)
    if not isinstance(raw, dict):
        return None
    return raw


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
    if isinstance(v, (int, float)):
        ts = float(v)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _norm_ca(ca: str) -> str:
    return (ca or "").strip()


def _text_mentions_ca(text: str, mint: str) -> bool:
    """True when post text contains the exact mint CA (not ticker-only)."""
    mint_n = _norm_ca(mint)
    if not mint_n or not text:
        return False
    if mint_n in text:
        return True
    # Also accept regex-extracted CA match equal to mint
    for m in _CA_PAT.finditer(text):
        if m.group(1) == mint_n:
            return True
    return False


def _status_id_from_url(url: str) -> Optional[str]:
    m = _STATUS_URL_RE.search(url or "")
    if not m:
        return None
    # last path segment
    parts = (url or "").rstrip("/").split("/")
    return parts[-1] if parts else None


def _is_pump_twitter_reuse(
    post: dict[str, Any], pump_twitter: Optional[str], mint: Optional[str] = None
) -> bool:
    """Reject posts that only re-share the pump.fun twitter status/profile URL.

    Common farm pattern: paste CA + the same pump.twitter status link with no
    real discussion. Counts as URL reuse, not organic narrative.
    """
    if not pump_twitter or not is_real_social_url(pump_twitter):
        return False
    pump_u = pump_twitter.strip().split("?")[0].rstrip("/").lower()
    pump_u = pump_u.replace("twitter.com", "x.com")
    pump_sid = _status_id_from_url(pump_twitter)
    text = str(post.get("text") or "")
    url = str(post.get("url") or "")
    blob = f"{text} {url}".lower().replace("twitter.com", "x.com")
    hit = False
    if pump_u and pump_u in blob:
        hit = True
    if pump_sid and pump_sid in blob:
        hit = True
    if not hit:
        return False
    # Strip URLs + the mint CA + whitespace/punctuation — leftover should be real prose
    stripped = re.sub(r"https?://\S+", "", text)
    if mint:
        stripped = stripped.replace(mint, "")
    stripped = re.sub(r"[\s|.,;:!?\-_/\"']+", " ", stripped).strip()
    # No meaningful discussion beyond CA + pump status paste
    return len(stripped) < 16


def _author_looks_farm(author: Any) -> bool:
    if author is None:
        return False
    if isinstance(author, dict):
        handle = str(author.get("username") or author.get("name") or "")
    else:
        handle = str(author)
    handle = handle.lstrip("@").strip()
    if not handle:
        return False
    return bool(_FARM_AUTHOR_RE.search(handle))


def evaluate_organic_payload(
    mint: str,
    payload: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    pump_twitter: Optional[str] = None,
    max_age_hours: float = ORGANIC_X_MAX_AGE_HOURS,
) -> dict[str, Any]:
    """Score a disk payload → organic_x / x_social hints + match details.

    Requires ≥1 recent post (<max_age_hours) whose text mentions the mint CA.
    Rejects ticker-only spam, airdrop templates, and pure pump.twitter URL reuse.
    Prefers non-status-farm authors (soft — farm authors alone do not kill a
    good CA mention, but all-farm matches downgrade organic_x).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    mint_n = _norm_ca(mint)
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if not isinstance(posts, list):
        posts = []

    matching: list[dict[str, Any]] = []
    farm_author_hits = 0
    reasons: list[str] = []

    cutoff = now - timedelta(hours=max_age_hours)

    for raw in posts:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "")
        created = _parse_dt(raw.get("created_at"))
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            continue
        if _AIRDROP_FARM_RE.search(text):
            continue
        if not _text_mentions_ca(text, mint_n):
            continue
        if _is_pump_twitter_reuse(raw, pump_twitter, mint=mint_n):
            continue
        author = raw.get("author")
        if _author_looks_farm(author):
            farm_author_hits += 1
        matching.append(raw)

    if not matching:
        reasons.append("no_recent_ca_mention")
        return {
            "organic_x": False,
            "x_social": False,
            "matching_n": 0,
            "farm_author_n": 0,
            "reasons": reasons,
            "matching_posts": [],
        }

    # Prefer non-farm authors: if EVERY match is a farm author, do not stamp organic
    non_farm = [p for p in matching if not _author_looks_farm(p.get("author"))]
    if not non_farm and farm_author_hits == len(matching):
        reasons.append("only_status_farm_authors")
        return {
            "organic_x": False,
            "x_social": True,  # weak X signal present but not organic
            "matching_n": len(matching),
            "farm_author_n": farm_author_hits,
            "reasons": reasons,
            "matching_posts": matching[:5],
        }

    reasons.append("recent_ca_mention")
    if non_farm:
        reasons.append("non_farm_author")
    return {
        "organic_x": True,
        "x_social": True,
        "matching_n": len(matching),
        "farm_author_n": farm_author_hits,
        "reasons": reasons,
        "matching_posts": (non_farm or matching)[:5],
    }


def annotate_organic_x_hints(
    rec: Any,
    *,
    data_root: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Any:
    """Read ``data/x_organic/<ca>.json`` and merge organic_x / x_social hints.

    No-op when file missing. Never clears an existing ``organic_x=True``.
    """
    ca = (getattr(rec, "ca", None) or "").strip()
    if not ca:
        return rec
    payload = load_organic_payload(ca, data_root=data_root)
    if payload is None:
        return rec

    hints = dict(getattr(rec, "confidence_hints", None) or {})
    pump_tw = hints.get("pump_twitter") or hints.get("dex_twitter")
    if isinstance(pump_tw, str):
        pump_twitter = pump_tw
    else:
        pump_twitter = None

    result = evaluate_organic_payload(
        ca, payload, now=now, pump_twitter=pump_twitter
    )
    hints["organic_x_eval"] = {
        "matching_n": result["matching_n"],
        "farm_author_n": result["farm_author_n"],
        "reasons": result["reasons"],
        "queried_at": payload.get("queried_at"),
        "query": payload.get("query"),
    }
    if result.get("organic_x") is True:
        hints["organic_x"] = True
        hints["x_social"] = True
        # Attach a couple of refs for evidence
        refs = []
        for p in result.get("matching_posts") or []:
            u = p.get("url")
            if u:
                refs.append(str(u))
        if refs:
            hints["organic_x_refs"] = refs[:5]
    elif result.get("x_social") is True:
        hints.setdefault("x_social", True)
        # Explicit false so _organic_x_evidence does not treat bare x_social as organic
        if hints.get("organic_x") is not True:
            hints["organic_x"] = False
    else:
        # File present but no match — do not invent organic; leave prior hints
        if hints.get("organic_x") is not True:
            hints.setdefault("organic_x", False)

    rec.confidence_hints = hints

    # Ensure x_social appears in sources when organic evidence lands
    if result.get("organic_x") is True or result.get("x_social") is True:
        sources = list(getattr(rec, "sources", None) or [])
        if "x_social" not in sources:
            sources.append("x_social")
            try:
                rec.sources = sources
            except Exception:  # noqa: BLE001
                pass
    return rec


def strong_unique_curve_profile(rec: Any, hints: Optional[dict[str, Any]] = None) -> bool:
    """Strong unique curve+profile — alternate Jacob-ping path without organic_x.

    Requires bonding-curve context, non-status-spam twitter (profile or tw+tg),
    unique ticker, and not a known farm/clone storm. Keeps HELLO floods suppressed.
    """
    h = hints if hints is not None else dict(getattr(rec, "confidence_hints", None) or {})
    if h.get("clone_storm") is True or h.get("spam_farm_ticker") is True:
        return False
    if h.get("parasite") is True or h.get("parasite_of_runner") is True:
        return False
    if h.get("airdrop_farm") is True:
        return False
    sources = [s for s in (getattr(rec, "sources", None) or []) if s]
    has_curve = (
        "pumpfun_curve" in sources
        or h.get("pump_curve") is True
        or getattr(rec, "curve_progress", None) is not None
        or str(getattr(rec, "ca", "") or "").endswith("pump")
    )
    if not has_curve:
        return False
    if not non_status_spam_twitter(h):
        return False
    if h.get("ticker_unique_recent") is not True:
        return False
    mc = getattr(rec, "mc_usd", None)
    if mc is not None:
        try:
            if float(mc) <= 0 or float(mc) >= 1_000_000.0:
                return False
        except (TypeError, ValueError):
            return False
    return True


__all__ = [
    "ORGANIC_X_MAX_AGE_HOURS",
    "annotate_organic_x_hints",
    "ca_search_query",
    "empty_organic_payload",
    "evaluate_organic_payload",
    "load_organic_payload",
    "organic_x_dir",
    "organic_x_path",
    "organic_x_required_for_ping",
    "strong_unique_curve_profile",
    "write_organic_payload",
    "x_social_live_enabled",
]
