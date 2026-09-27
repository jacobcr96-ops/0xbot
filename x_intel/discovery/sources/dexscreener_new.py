"""DexScreener newest profiles/pairs — Solana+Base+ETH+BSC minimum.

Solana pump.fun stays the primary early path. ``XINTEL_SKIP_DEX`` skips the
*Solana* Dex poll (rate-limit relief) but EVM chains (bsc/base/ethereum) still
poll on a longer interval / separate cooldown so coins like CROW get first-sight
without pasting a CA.

Env:
  XINTEL_CHAINS=solana,bsc,base   — filter which chains emit events
  XINTEL_SKIP_DEX=1               — skip Solana Dex profiles/boosts only
  XINTEL_DEX_EVM_INTERVAL_SEC     — min seconds between EVM polls (default 300)
  XINTEL_DEX_EVM_COOLDOWN_SEC     — post-429 EVM cooldown (default 2700)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from x_intel.config import (
    EVM_CHAINS,
    configured_chains,
    data_dir,
    evm_dex_cooldown_sec,
    evm_dex_min_interval_sec,
)
from x_intel.discovery.models import DiscoveryEvent

log = logging.getLogger(__name__)

# Public DexScreener endpoints (no API key). Live mode optional.
TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
# Optional BSC new-pair probe via WBNB token pairs (sorted by pairCreatedAt)
BSC_WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{ca}"

CHAIN_MAP = {
    "solana": "solana",
    "base": "base",
    "ethereum": "ethereum",
    "bsc": "bsc",
    "eth": "ethereum",
}

DEFAULT_CHAINS = frozenset({"solana", "base", "ethereum", "bsc"})


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def skip_dex_solana() -> bool:
    """XINTEL_SKIP_DEX — historically killed all Dex; now Solana-only."""
    return _env_bool("XINTEL_SKIP_DEX", default=False)


class DexScreenerNewSource:
    source_id = "dexscreener_new"

    def __init__(
        self,
        *,
        fixture_dir: Optional[Path] = None,
        live: bool = False,
        chains: Optional[set[str]] = None,
        timeout_s: float = 8.0,
        data_dir_path: Optional[Path] = None,
        include_bsc_new_pairs: bool = True,
    ) -> None:
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.live = live
        # Prefer explicit ctor chains; else env XINTEL_CHAINS; else DEFAULT
        if chains is not None:
            self.chains = set(chains)
        else:
            self.chains = set(configured_chains())
        self.timeout_s = timeout_s
        self.data_dir_path = Path(data_dir_path) if data_dir_path else None
        self.include_bsc_new_pairs = include_bsc_new_pairs

    def _health_dir(self) -> Path:
        if self.data_dir_path is not None:
            return Path(self.data_dir_path) / "health"
        env = os.environ.get("XINTEL_DATA_DIR", "").strip()
        if env:
            return Path(env) / "health"
        try:
            return data_dir() / "health"
        except Exception:  # noqa: BLE001
            return Path("data") / "health"

    def poll(self) -> list[DiscoveryEvent]:
        if not self.live:
            return self._poll_fixture()
        try:
            return self._poll_live()
        except Exception as e:  # noqa: BLE001 — discovery must not crash runner
            msg = str(e)
            log.warning("dexscreener_new live poll failed: %s — no fixture fallback in live mode", e)
            if "429" in msg or "Too Many Requests" in msg:
                self._arm_cooldown(solana=True, evm=True)
            return []

    def _poll_fixture(self) -> list[DiscoveryEvent]:
        path = self._fixture_path()
        if path is None or not path.is_file():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("pairs") or raw.get("events") or []
        return [ev for r in rows if (ev := self._row_to_event(r))]

    def _fixture_path(self) -> Optional[Path]:
        if self.fixture_dir:
            p = self.fixture_dir / "dexscreener_new.json"
            if p.is_file():
                return p
        here = Path(__file__).resolve()
        for parent in here.parents:
            cand = parent / "tests" / "fixtures" / "discovery" / "dexscreener_new.json"
            if cand.is_file():
                return cand
        return None

    def _cooldown_path(self, kind: str) -> Path:
        return self._health_dir() / f"dex_{kind}_cooldown_until"

    def _last_poll_path(self, kind: str) -> Path:
        return self._health_dir() / f"dex_{kind}_last_poll"

    def _in_cooldown(self, kind: str) -> bool:
        path = self._cooldown_path(kind)
        # Also honor legacy single cooldown file for solana path
        paths = [path]
        if kind == "solana":
            paths.append(self._health_dir() / "dex_cooldown_until")
        for p in paths:
            try:
                if p.is_file() and time.time() < float(p.read_text().strip() or "0"):
                    return True
            except Exception:
                continue
        return False

    def _arm_cooldown(self, *, solana: bool = False, evm: bool = False) -> None:
        health = self._health_dir()
        try:
            health.mkdir(parents=True, exist_ok=True)
            now = time.time()
            if solana:
                # 30 min Solana (legacy)
                (health / "dex_solana_cooldown_until").write_text(str(now + 30 * 60))
                (health / "dex_cooldown_until").write_text(str(now + 30 * 60))
                log.info("dexscreener_new solana cooldown armed 30m after 429")
            if evm:
                sec = evm_dex_cooldown_sec()
                (health / "dex_evm_cooldown_until").write_text(str(now + sec))
                log.info("dexscreener_new evm cooldown armed %.0fs after 429", sec)
        except Exception:
            pass

    def _interval_ok(self, kind: str, min_interval: float) -> bool:
        path = self._last_poll_path(kind)
        try:
            if path.is_file():
                last = float(path.read_text().strip() or "0")
                if time.time() - last < min_interval:
                    return False
        except Exception:
            pass
        return True

    def _mark_polled(self, kind: str) -> None:
        path = self._last_poll_path(kind)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(time.time()))
        except Exception:
            pass

    def _poll_live(self) -> list[DiscoveryEvent]:
        events: list[DiscoveryEvent] = []
        want_solana = "solana" in self.chains
        want_evm = bool(self.chains & EVM_CHAINS)

        # --- Solana Dex path (high frequency, skippable) ---
        if want_solana and not skip_dex_solana():
            if self._in_cooldown("solana"):
                log.info("dexscreener_new solana skipped (cooldown after 429)")
            else:
                try:
                    events.extend(self._poll_profiles_boosts(chains={"solana"}))
                    self._mark_polled("solana")
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    log.warning("dexscreener_new solana poll failed: %s", e)
                    if "429" in msg or "Too Many Requests" in msg:
                        self._arm_cooldown(solana=True)
        elif want_solana and skip_dex_solana():
            log.info("dexscreener_new solana skipped (XINTEL_SKIP_DEX) — pump.fun primary")

        # --- EVM Dex path (lower frequency, separate cooldown) ---
        evm_chains = self.chains & EVM_CHAINS
        if want_evm and evm_chains:
            if self._in_cooldown("evm"):
                log.info("dexscreener_new evm skipped (cooldown after 429)")
            elif not self._interval_ok("evm", evm_dex_min_interval_sec()):
                log.info(
                    "dexscreener_new evm skipped (interval < %.0fs)",
                    evm_dex_min_interval_sec(),
                )
            else:
                try:
                    events.extend(self._poll_profiles_boosts(chains=evm_chains))
                    if self.include_bsc_new_pairs and "bsc" in evm_chains:
                        events.extend(self._poll_bsc_new_pairs())
                    self._mark_polled("evm")
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    log.warning("dexscreener_new evm poll failed: %s", e)
                    if "429" in msg or "Too Many Requests" in msg:
                        self._arm_cooldown(evm=True)

        return events

    def _http_json(self, url: str) -> Any:
        req = Request(url, headers={"User-Agent": "x-intel-discovery/0.1"})
        with urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
            if getattr(resp, "status", 200) == 429:
                raise HTTPError(url, 429, "Too Many Requests", hdrs=None, fp=None)  # type: ignore[arg-type]
            return json.loads(resp.read().decode("utf-8"))

    def _poll_profiles_boosts(self, *, chains: set[str]) -> list[DiscoveryEvent]:
        events: list[DiscoveryEvent] = []
        for url in (TOKEN_PROFILES_URL, TOKEN_BOOSTS_URL):
            is_boost = url == TOKEN_BOOSTS_URL
            payload = self._http_json(url)
            rows = payload if isinstance(payload, list) else []
            for r in rows:
                ev = self._profile_to_event(r, from_boost=is_boost, chain_allow=chains)
                if ev:
                    events.append(ev)
        return events

    def _poll_bsc_new_pairs(self) -> list[DiscoveryEvent]:
        """Optional BSC newest pairs via WBNB token endpoint — respects shared EVM cooldown."""
        url = DEX_TOKEN_URL.format(ca=BSC_WBNB)
        try:
            payload = self._http_json(url)
        except Exception as e:  # noqa: BLE001
            log.debug("bsc new-pair probe failed: %s", e)
            raise
        pairs = payload.get("pairs") or [] if isinstance(payload, dict) else []
        # Newest first; cap to avoid flood
        scored: list[tuple[float, dict[str, Any]]] = []
        for p in pairs:
            if not isinstance(p, dict):
                continue
            if str(p.get("chainId") or "").lower() != "bsc":
                continue
            created = p.get("pairCreatedAt")
            try:
                ts = float(created) if created is not None else 0.0
                if ts > 1e12:
                    ts /= 1000.0
            except (TypeError, ValueError):
                ts = 0.0
            scored.append((ts, p))
        scored.sort(key=lambda t: t[0], reverse=True)
        events: list[DiscoveryEvent] = []
        now = datetime.now(timezone.utc)
        for ts, p in scored[:15]:
            base = p.get("baseToken") or {}
            ca = (base.get("address") or "").strip()
            if not ca:
                continue
            # Skip WBNB itself
            if ca.lower() == BSC_WBNB.lower():
                continue
            pair_created = (
                datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else None
            )
            events.append(
                DiscoveryEvent(
                    source=self.source_id,
                    discovered_at=now,
                    chain="bsc",
                    ca=ca,
                    ticker=base.get("symbol"),
                    name=base.get("name"),
                    symbol=base.get("symbol"),
                    raw_ref=p.get("url") or p.get("pairAddress"),
                    mc_usd=_f(p.get("marketCap") or p.get("fdv")),
                    liquidity_usd=_f((p.get("liquidity") or {}).get("usd")),
                    pair_created_at=pair_created,
                    event_kind="new_pair",
                    confidence_hints={
                        "dexscreener": True,
                        "thin_dex_new": True,
                        "bsc_wbnb_probe": True,
                    },
                )
            )
        return events

    def _profile_to_event(
        self,
        r: dict[str, Any],
        *,
        from_boost: bool = False,
        chain_allow: Optional[set[str]] = None,
    ) -> Optional[DiscoveryEvent]:
        chain_id = str(r.get("chainId") or r.get("chain") or "").lower()
        chain = CHAIN_MAP.get(chain_id, chain_id)
        allow = chain_allow if chain_allow is not None else self.chains
        if chain not in allow or chain not in self.chains:
            return None
        ca = (r.get("tokenAddress") or r.get("ca") or "").strip()
        if not ca:
            return None
        # Never emit EVM ca under solana (or vice versa)
        if chain == "solana" and ca.lower().startswith("0x"):
            return None
        if chain in EVM_CHAINS and not ca.lower().startswith("0x"):
            return None
        now = datetime.now(timezone.utc)
        hints: dict[str, Any] = {"dexscreener": True, "thin_dex_new": True}
        if from_boost:
            hints["boost_only"] = True
            hints["paid_boost"] = True
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=now,
            chain=chain,
            ca=ca,
            ticker=r.get("symbol") or r.get("ticker"),
            raw_ref=r.get("url") or r.get("description") or url_safe(r),
            mc_usd=_f(r.get("mc_usd") or r.get("marketCap")),
            liquidity_usd=_f(r.get("liquidity_usd")),
            event_kind="boost" if from_boost else "new_profile",
            confidence_hints=hints,
        )

    def _row_to_event(self, r: dict[str, Any]) -> Optional[DiscoveryEvent]:
        if not isinstance(r, dict):
            return None
        chain = str(r.get("chain") or "solana").lower()
        if chain not in self.chains:
            return None
        ca = (r.get("ca") or r.get("tokenAddress") or r.get("contract_address") or "").strip()
        if not ca:
            return None
        if chain == "solana" and ca.lower().startswith("0x"):
            return None
        if chain in EVM_CHAINS and not ca.lower().startswith("0x"):
            return None
        discovered = _parse_dt(r.get("discovered_at") or r.get("pairCreatedAt")) or datetime.now(
            timezone.utc
        )
        pair_created = _parse_dt(r.get("pair_created_at") or r.get("pairCreatedAt"))
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=discovered,
            chain=chain,
            ca=ca,
            ticker=r.get("ticker") or r.get("symbol"),
            raw_ref=r.get("raw_ref") or r.get("url") or r.get("pairAddress"),
            mc_usd=_f(r.get("mc_usd") or r.get("marketCap")),
            liquidity_usd=_f(r.get("liquidity_usd") or _nested(r, "liquidity", "usd")),
            pair_created_at=pair_created,
            event_kind=r.get("event_kind") or "new_pair",
            confidence_hints=dict(r.get("confidence_hints") or {"dexscreener": True, "thin_dex_new": True}),
        )


def url_safe(r: dict[str, Any]) -> str:
    return str(r.get("url") or "")


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nested(d: dict, *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        ts = float(v)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
