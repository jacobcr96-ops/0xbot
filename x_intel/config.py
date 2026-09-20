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
            if k and k not in os.environ:
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
DEFAULT_EARLY_MC_USD_MAX = 500_000.0

# Default size stub when emitting BUY from pursue.
DEFAULT_BUY_PERCENT_EQUITY = 1.0
# FOMO min notional ~$2.10; on ~$300 equity need >=~0.7%
MIN_BUY_PERCENT_EQUITY = 0.75
