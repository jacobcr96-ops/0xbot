"""Earliness validation harness — historical + prospective.

Historical: simulate when each source would have fired vs known T0 / peak.
Prospective: discovery_latency_features already logged on live discovers.

Be honest when minute-level historical data is missing.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from x_intel.ledger.store import default_paths

# Case files with extractable launch windows (BATCH_01)
# peak times are coarse — often days later; we report honesty gaps.

CASE_SPECS: list[dict[str, Any]] = [
    {
        "slug": "runner_pnut_sol",
        "ticker": "PNUT",
        "chain": "solana",
        "ca": "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump",
        "t0": "2024-10-31T14:21:40+00:00",
        "peak_approx": "2024-11-20T00:00:00+00:00",
        "peak_mc_usd": 530_000_000,
        "fomo_lag_baseline_hours": 48.0,  # FOMO-style discovery typically days after T0
        "minute_data": False,
        "notes": "Pump.fun T0 known; peak from Decrypt Binance window — day-granularity.",
    },
    {
        "slug": "runner_moodeng_sol",
        "ticker": "MOODENG",
        "chain": "solana",
        "ca": None,  # fill from case if present
        "t0": "2024-09-10T00:00:00+00:00",
        "peak_approx": "2024-09-20T00:00:00+00:00",
        "peak_mc_usd": None,
        "fomo_lag_baseline_hours": 36.0,
        "minute_data": False,
        "notes": "T0 day-level only; exact mint minute UNKNOWN in open sources.",
    },
    {
        "slug": "runner_goat_sol",
        "ticker": "GOAT",
        "chain": "solana",
        "ca": None,
        "t0": "2024-10-10T00:00:00+00:00",
        "peak_approx": "2024-10-24T00:00:00+00:00",
        "peak_mc_usd": None,
        "fomo_lag_baseline_hours": 24.0,
        "minute_data": False,
        "notes": "Truth Terminal amplifier window — minute MC series missing.",
    },
    {
        "slug": "fail_shar_sol",
        "ticker": "SHAR",
        "chain": "solana",
        "ca": None,
        "t0": "2024-03-01T00:00:00+00:00",
        "peak_approx": "2024-03-01T01:00:00+00:00",
        "peak_mc_usd": None,
        "fomo_lag_baseline_hours": 0.5,
        "minute_data": False,
        "notes": "Death exemplar D1 sybil — early social heat ≠ quality.",
    },
]


def _parse_case_ca(case_path: Path) -> Optional[str]:
    if not case_path.is_file():
        return None
    text = case_path.read_text(encoding="utf-8")
    m = re.search(r"contract_address:\s*`?([A-Za-z0-9]{32,44})`?", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([1-9A-HJ-NP-Za-km-z]{32,44}pump)\b", text)
    return m.group(1) if m else None


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def simulate_source_fire_times(spec: dict[str, Any]) -> dict[str, Any]:
    """Simulate earliest practical fire per source class relative to T0.

    Without minute replay feeds we use *policy assumptions* labeled as such:
    - dexscreener_new / pumpfun_curve: T0 + 1–5 min (pair index lag)
    - flow_hint: T0 + 5–15 min (cluster tooling)
    - x_social: T0 + 10–60 min (CA propagation)
    - fomo_sidebar: T0 + fomo_lag_baseline_hours (lagging universe)
    """
    t0 = _parse_dt(spec["t0"])
    peak = _parse_dt(spec["peak_approx"]) if spec.get("peak_approx") else None
    fomo_h = float(spec.get("fomo_lag_baseline_hours") or 48.0)

    assumptions = {
        "pumpfun_curve_min_after_t0": 2.0,
        "dexscreener_new_min_after_t0": 5.0,
        "flow_hint_min_after_t0": 10.0,
        "x_social_min_after_t0": 30.0,
        "fomo_sidebar_hours_after_t0": fomo_h,
    }

    fires = {
        "pumpfun_curve": t0.timestamp() + assumptions["pumpfun_curve_min_after_t0"] * 60,
        "dexscreener_new": t0.timestamp() + assumptions["dexscreener_new_min_after_t0"] * 60,
        "flow_hint": t0.timestamp() + assumptions["flow_hint_min_after_t0"] * 60,
        "x_social": t0.timestamp() + assumptions["x_social_min_after_t0"] * 60,
        "fomo_sidebar": t0.timestamp() + assumptions["fomo_sidebar_hours_after_t0"] * 3600,
    }

    def lead_hours(ts: float, vs: Optional[datetime]) -> Optional[float]:
        if vs is None:
            return None
        return (vs.timestamp() - ts) / 3600.0

    rows = []
    for src, ts in fires.items():
        rows.append(
            {
                "source": src,
                "fire_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "lead_vs_peak_hours": lead_hours(ts, peak),
                "lag_vs_t0_minutes": (ts - t0.timestamp()) / 60.0,
                "lead_vs_fomo_hours": (fires["fomo_sidebar"] - ts) / 3600.0,
            }
        )

    return {
        "slug": spec["slug"],
        "ticker": spec.get("ticker"),
        "t0": t0.isoformat(),
        "peak_approx": peak.isoformat() if peak else None,
        "minute_data_available": bool(spec.get("minute_data")),
        "honesty": (
            "MINUTE-LEVEL HISTORICAL DATA MISSING — fire times are policy assumptions "
            "for source ordering, not measured DexScreener/X archives."
            if not spec.get("minute_data")
            else "Measured from case timestamps."
        ),
        "notes": spec.get("notes"),
        "assumptions": assumptions,
        "source_fires": rows,
        "earliest_source": min(rows, key=lambda r: r["lag_vs_t0_minutes"])["source"],
        "fomo_lag_hours": fomo_h,
    }


def load_enriched_specs(reports_dir: Path) -> list[dict[str, Any]]:
    out = []
    for spec in CASE_SPECS:
        s = dict(spec)
        case_path = reports_dir / "cases" / f"{spec['slug']}.md"
        if not s.get("ca"):
            s["ca"] = _parse_case_ca(case_path)
        out.append(s)
    return out


def generate_report(reports_dir: Optional[Path] = None) -> str:
    paths = default_paths()
    reports_dir = reports_dir or paths.reports
    specs = load_enriched_specs(reports_dir)
    simulations = [simulate_source_fire_times(s) for s in specs]

    lines: list[str] = []
    lines.append("# Early Discovery Validation v0")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()} (UTC)")
    lines.append(f"**Experiment:** `xintel_v0` / discovery workstream")
    lines.append("")
    lines.append("## Honesty preamble")
    lines.append("")
    lines.append(
        "BATCH_01 case files mostly lack minute-level MC/liq/X series at T0. "
        "Where minute data is missing, source fire times below are **labeled policy "
        "assumptions** (ordering: pump/dex → flow → X → FOMO), not backtested archive hits. "
        "Do not treat lead-time hours as measured alpha."
    )
    lines.append("")
    lines.append("## Threshold constants (gates.py)")
    lines.append("")
    lines.append("| Constant | Value | Rationale |")
    lines.append("|----------|-------|-----------|")
    lines.append("| `AGE_MINUTES_PURSUE_MAX` | 30 | Early edge dies after first half-hour of pair life in BATCH pump cohort. |")
    lines.append("| `MC_USD_PUMP_CURVE_MAX` | 250_000 | Curve tokens past ~$250k are usually mid-print; PNUT early was <<$10k. |")
    lines.append("| `MC_USD_DEX_NEW_MAX` | 1_000_000 | Listed dex-new with liq can still be early under $1M. |")
    lines.append("| `MIN_LIQ_USD_AFTER_ENRICH` | 500 | Dead micros with ~0 liq are untradable rejects. |")
    lines.append("| `MC_USD_HARD_REJECT` | 5_000_000 | Past $5M is not early-mover discovery. |")
    lines.append("| FOMO-only | hard reject | Sidebar is lagging confirmation — never sole pursue. |")
    lines.append("| D1/sybil | hard reject | BATCH death exemplar SHAR. |")
    lines.append("")
    lines.append("## Historical simulations")
    lines.append("")

    for sim in simulations:
        lines.append(f"### {sim['slug']} ({sim.get('ticker')})")
        lines.append("")
        lines.append(f"- **T0:** {sim['t0']}")
        lines.append(f"- **Peak approx:** {sim['peak_approx']}")
        lines.append(f"- **Minute data:** {sim['minute_data_available']}")
        lines.append(f"- **Honesty:** {sim['honesty']}")
        lines.append(f"- **Earliest assumed source:** `{sim['earliest_source']}`")
        lines.append(f"- **FOMO lag baseline:** {sim['fomo_lag_hours']}h after T0")
        if sim.get("notes"):
            lines.append(f"- **Notes:** {sim['notes']}")
        lines.append("")
        lines.append("| Source | Lag vs T0 (min) | Lead vs FOMO (h) | Lead vs peak (h) |")
        lines.append("|--------|-----------------|------------------|------------------|")
        for row in sim["source_fires"]:
            peak_lead = row["lead_vs_peak_hours"]
            peak_s = f"{peak_lead:.1f}" if peak_lead is not None else "n/a"
            lines.append(
                f"| `{row['source']}` | {row['lag_vs_t0_minutes']:.1f} | "
                f"{row['lead_vs_fomo_hours']:.2f} | {peak_s} |"
            )
        lines.append("")

    lines.append("## Prospective metrics")
    lines.append("")
    lines.append(
        "Every live `ingest_event` writes `discovery_latency_features` onto the bus record "
        "and candidate extras: `{source, first_seen_at, mc_at_first_seen, liquidity_usd, enriched_at}`."
    )
    lines.append("")
    lines.append("Prospective ledger path: `data/candidates/*.json` → `discovery.discovery_latency_features`.")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("```bash")
    lines.append("make discovery-validate")
    lines.append("# or")
    lines.append("python -m x_intel.discovery.validate")
    lines.append("```")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_report(out_path: Optional[Path] = None) -> Path:
    paths = default_paths()
    out = out_path or (paths.reports / "early_discovery_validation_v0.md")
    text = generate_report(paths.reports)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate early discovery validation report")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    path = write_report(args.out)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
