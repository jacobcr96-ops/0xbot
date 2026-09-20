"""Historical case / hypothesis loaders (read-only research artifacts)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from x_intel.ledger.store import default_paths


def list_case_files(reports_dir: Optional[Path] = None) -> list[Path]:
    root = reports_dir or default_paths().reports
    cases = root / "cases"
    if not cases.is_dir():
        return []
    return sorted(cases.glob("*.md"))


def load_case_index(reports_dir: Optional[Path] = None) -> str:
    root = reports_dir or default_paths().reports
    path = root / "BATCH_01_INDEX.md"
    return path.read_text() if path.exists() else ""


def load_hypotheses_path(reports_dir: Optional[Path] = None) -> Path:
    root = reports_dir or default_paths().reports
    return root / "signal_hypotheses_v0.md"


def findings_summary(reports_dir: Optional[Path] = None) -> dict:
    root = reports_dir or default_paths().reports
    cases = list_case_files(root)
    return {
        "batch_01_index": str(root / "BATCH_01_INDEX.md"),
        "batch_01_signals": str(root / "BATCH_01_SIGNALS.md"),
        "signal_hypotheses": str(root / "signal_hypotheses_v0.md"),
        "signal_leaderboard": str(root / "signal_leaderboard_v0.md"),
        "historical_cases": str(root / "historical_cases_v0.md"),
        "n_case_files": len(cases),
        "case_slugs": [p.stem for p in cases],
    }
