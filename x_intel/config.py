"""Runtime config for x_intel — data paths and live-risk arming.

XINTEL_ARMED is an intentional live-risk switch. Default false: decisions still
emit to disk/API for paper trading, but do_not_execute_until_armed MUST be true.
When true, newly emitted decisions set do_not_execute_until_armed=false.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _load_repo_env() -> None:
    """Load simple KEY=VAL lines from repo .env if present (no python-dotenv required)."""
    try:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if not env_path.is_file():
            return
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if not k:
                continue
            # .env is source of truth for arming; always apply XINTEL_* from file.
            if k.startswith("XINTEL_") or k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


_load_repo_env()



def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_armed() -> bool:
    """True only when XINTEL_ARMED is explicitly enabled.

    Arming is the intentional live-risk switch for 0xbot execution consumers.
    """
    return _env_bool("XINTEL_ARMED", default=False)


def do_not_execute_until_armed() -> bool:
    """Flag value stamped onto newly emitted decisions."""
    return not is_armed()


def data_dir(start: Optional[Path] = None) -> Path:
    """Root for candidates/decisions/outcomes/execution_reports.

    Env XINTEL_DATA_DIR overrides (absolute or relative to process cwd).
    Default: <repo>/data/
    """
    override = os.environ.get("XINTEL_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # Walk up to repo root (has data/candidates or pyproject with x-intel)
    here = (start or Path(__file__)).resolve()
    for p in [here, *here.parents]:
        if (p / "data" / "candidates").is_dir():
            return (p / "data").resolve()
        if (p / "pyproject.toml").is_file() and (p / "data").is_dir():
            return (p / "data").resolve()
    return (Path(__file__).resolve().parents[1] / "data").resolve()


# BUY/ADD hard TTL — git handoff can cost ~3+ minutes; 20 minutes avoids false stale.
BUY_ADD_TTL_SECONDS = 1200

# Early MC gate for pursue→BUY (null MC allowed with warning).
# Align with discovery pump/dex actionable early ceiling ($1M) so SCAT/ZEBRA-class
# first-sights under $1M are not blocked at emit after quality pass.
DEFAULT_EARLY_MC_USD_MAX = 1_000_000.0

# Default size stub when emitting BUY from pursue.
DEFAULT_BUY_PERCENT_EQUITY = 1.0
# FOMO min notional ~$2.10; on ~$300 equity need >=~0.7%
MIN_BUY_PERCENT_EQUITY = 0.75


def x_social_live_enabled() -> bool:
    """Scans should set XINTEL_X_SOCIAL_LIVE=1 for CA-scoped organic X path."""
    return _env_bool("XINTEL_X_SOCIAL_LIVE", default=False)


def organic_x_required_for_ping() -> bool:
    """Jacob ping / xintel publish prefer organic_x when enabled."""
    return _env_bool("XINTEL_ORGANIC_X_REQUIRED_FOR_PING", default=False)



# ---------------------------------------------------------------------------
# Multi-chain discovery / quotes
# ---------------------------------------------------------------------------

DEFAULT_DISCOVERY_CHAINS: tuple[str, ...] = ("solana", "base", "ethereum", "bsc")
EVM_CHAINS = frozenset(
    {"ethereum", "base", "bsc", "arbitrum", "polygon", "avalanche", "optimism"}
)

# GoPlus token_security chain_id
GOPLUS_CHAIN_IDS: dict[str, str] = {
    "ethereum": "1",
    "eth": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
}

# DexScreener chainId strings
DEX_CHAIN_IDS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "ethereum",
    "eth": "ethereum",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "avalanche": "avalanche",
    "optimism": "optimism",
}


def configured_chains() -> frozenset[str]:
    """Chains enabled for Dex / multi-chain discovery.

    Env ``XINTEL_CHAINS=solana,bsc,base`` (comma-separated). Default: solana+base+ethereum+bsc.
    """
    raw = os.environ.get("XINTEL_CHAINS", "").strip()
    if not raw:
        return frozenset(DEFAULT_DISCOVERY_CHAINS)
    out: set[str] = set()
    aliases = {
        "sol": "solana",
        "eth": "ethereum",
        "ether": "ethereum",
        "bnb": "bsc",
        "binance": "bsc",
    }
    for part in raw.split(","):
        c = part.strip().lower()
        if not c:
            continue
        out.add(aliases.get(c, c))
    return frozenset(out) if out else frozenset(DEFAULT_DISCOVERY_CHAINS)


def is_evm_chain(chain: Optional[str]) -> bool:
    return (chain or "").strip().lower() in EVM_CHAINS


def goplus_chain_id(chain: str) -> Optional[str]:
    return GOPLUS_CHAIN_IDS.get((chain or "").strip().lower())


def dex_chain_id(chain: str) -> Optional[str]:
    return DEX_CHAIN_IDS.get((chain or "").strip().lower())


def evm_dex_min_interval_sec() -> float:
    """Min seconds between EVM DexScreener discovery polls (Solana pump stays primary)."""
    raw = os.environ.get("XINTEL_DEX_EVM_INTERVAL_SEC", "").strip()
    if not raw:
        return 300.0  # 5 minutes
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 300.0


def evm_dex_cooldown_sec() -> float:
    """Cooldown after Dex 429 for EVM polls (longer than Solana path)."""
    raw = os.environ.get("XINTEL_DEX_EVM_COOLDOWN_SEC", "").strip()
    if not raw:
        return 45 * 60.0  # 45 min
    try:
        return max(300.0, float(raw))
    except ValueError:
        return 45 * 60.0


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def max_ingest_per_cycle() -> int:
    """Per-cycle cap on ingested discovery events (``XINTEL_MAX_INGEST_PER_CYCLE``).

    Enrich costs ~1s/mint, so an uncapped post-outage backlog can stall a cycle
    for many minutes. Remainder is carried forward via the pump cursor backlog.
    """
    return int(_env_float("XINTEL_MAX_INGEST_PER_CYCLE", 150.0, minimum=1.0))


def ingest_budget_sec() -> float:
    """Wall-clock budget (from cycle start) after which ingest stops (``XINTEL_INGEST_BUDGET_SEC``)."""
    return _env_float("XINTEL_INGEST_BUDGET_SEC", 200.0, minimum=5.0)


def ingest_stale_skip_sec() -> float:
    """When over the cap, backlog mints older than this are skipped (``XINTEL_INGEST_STALE_SKIP_SEC``)."""
    return _env_float("XINTEL_INGEST_STALE_SKIP_SEC", 30 * 60.0, minimum=60.0)
