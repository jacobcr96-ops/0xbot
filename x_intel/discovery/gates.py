"""Early-discovery scoring gates — thresholds from BATCH_01–03 evidence.

Documented in docs/EARLY_DISCOVERY.md. Encode constants here; do not invent
OOS precision claims. FOMO-only discovery NEVER sole pursue trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from x_intel.discovery.models import DiscoveryRecord, KNOWN_FARM_DOMAINS
from x_intel.discovery.bus import is_resolvable_ca
from x_intel.schemas.models import CandidateDecision

# ---------------------------------------------------------------------------
# Threshold constants (chosen from BATCH_01–03 / historian case evidence)
# ---------------------------------------------------------------------------

# Prefer pursue when pair age from create/first-seen is still early.
# BATCH: PNUT/MOODENG/GOAT edge was hours-from-mint, not days-late FOMO.
AGE_MINUTES_PURSUE_MAX = 30.0

# Soft watch upper age — still interesting but late for first size.
AGE_MINUTES_WATCH_MAX = 180.0

# Pump.fun / bonding-curve early MC band (BATCH: Decrypt PNUT early ~<$10k;
# practical pursue ceiling before "already printed" for curve tokens).
MC_USD_PUMP_CURVE_MAX = 250_000.0

# Dex-new pairs already on AMM: wider early band if liquidity present.
# BATCH: many runners still sub-$1M when first liquid on DexScreener.
MC_USD_DEX_NEW_MAX = 1_000_000.0

# After enrich, zero/near-zero liq microcaps are dead/untradable → reject.
MIN_LIQ_USD_AFTER_ENRICH = 500.0

# Absolute hard ceiling — never pursue past this regardless of source.
MC_USD_HARD_REJECT = 5_000_000.0

# BUY TTL matches disk-queue contract (bridge RTT 5–15s).
BUY_TTL_SECONDS = 1200

# Known airdrop / farm URL fragments in raw_ref or hints
FARM_URL_FRAGMENTS = frozenset(
    {
        "airdrop",
        "claim-token",
        "free-mint",
        "presale-farm",
    }
)


@dataclass
class GateResult:
    decision: CandidateDecision
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pursue_eligible: bool = False

    @property
    def reject_reason(self) -> Optional[str]:
        if self.decision == CandidateDecision.reject and self.reasons:
            return self.reasons[0]
        return None


def _age_minutes(rec: DiscoveryRecord, now: Optional[datetime] = None) -> Optional[float]:
    now = now or datetime.now(timezone.utc)
    t0 = rec.pair_created_at or rec.first_seen_at
    if t0 is None:
        return None
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t0).total_seconds() / 60.0)


def _feature_hints(rec: DiscoveryRecord) -> dict[str, Any]:
    hints = dict(rec.confidence_hints or {})
    # Allow nested feature_scores style
    fs = hints.get("feature_scores") or hints.get("features") or {}
    if isinstance(fs, dict):
        for k, v in fs.items():
            if k not in hints:
                if isinstance(v, dict) and "value" in v:
                    hints[k] = v["value"]
                else:
                    hints[k] = v
    return hints


def _is_pump_curve_context(rec: DiscoveryRecord) -> bool:
    if rec.curve_progress is not None:
        return True
    if "pumpfun_curve" in (rec.sources or []):
        return True
    if rec.ca.endswith("pump"):
        return True
    return bool(rec.confidence_hints.get("pump_curve"))


def _fomo_only(rec: DiscoveryRecord) -> bool:
    """True when the ONLY discovery source(s) are lagging FOMO sidebar."""
    sources = [s for s in (rec.sources or []) if s]
    if not sources:
        return False
    non_fomo = [s for s in sources if s != "fomo_sidebar"]
    if non_fomo:
        return False
    return rec.lagging_universe or rec.first_source == "fomo_sidebar"


def _farm_domain_hit(rec: DiscoveryRecord) -> bool:
    blob = " ".join(rec.raw_refs or []).lower()
    domain_hints = rec.confidence_hints.get("domains") or []
    if isinstance(domain_hints, str):
        domain_hints = [domain_hints]
    for d in domain_hints:
        if str(d).lower() in KNOWN_FARM_DOMAINS:
            return True
    for frag in FARM_URL_FRAGMENTS:
        if frag in blob and "airdrop" in blob:
            return True
    if rec.confidence_hints.get("airdrop_farm") is True:
        return True
    return False



# Sources that alone do not justify a published BUY (thin / paid / lagging).
THIN_OR_PAID_SOURCES = frozenset({"dexscreener_new", "fomo_sidebar"})
QUALITY_SOURCES = frozenset({"x_social", "flow_hint", "pumpfun_curve"})

# risk_flags / hint keys that must never ride a real BUY emit
RETIRED_SHADOW_FLAGS = frozenset(
    {"calibration_shadow", "shadow_only", "pipe_check", "PIPECHECK", "post_move_not_early"}
)


def _sources(rec: DiscoveryRecord) -> list[str]:
    return [s for s in (rec.sources or []) if s]


def _organic_x_evidence(rec: DiscoveryRecord, hints: dict[str, Any]) -> bool:
    """Organic X evidence (not boost-only / paid-boost)."""
    if "x_social" not in _sources(rec) and not hints.get("x_social"):
        return False
    if hints.get("boost_only") is True or hints.get("paid_boost") is True:
        return False
    if hints.get("organic_x") is False:
        return False
    return True


def _first_buyer_flow_positive(rec: DiscoveryRecord, hints: dict[str, Any]) -> bool:
    """Positive first-buyer / flow desk signal (non-sybil)."""
    has_flow = "flow_hint" in _sources(rec) or hints.get("flow_hint") is True
    if not has_flow:
        return False
    if hints.get("sybil") is True or hints.get("D1") is True:
        return False
    if hints.get("flow_positive") is True:
        return True
    fbc = hints.get("first_buyer_count")
    if isinstance(fbc, (int, float)) and fbc > 0:
        return True
    # Explicit non-sybil flow hint counts as positive when no counter-signal
    if hints.get("sybil") is False:
        return True
    cluster = hints.get("cluster_score")
    if isinstance(cluster, (int, float)) and cluster < 0.5:
        return True
    return False


def _multi_channel_intel_pass(rec: DiscoveryRecord, hints: dict[str, Any]) -> bool:
    """Explicit multi-channel pass from intel (S1/S3 or dedicated flag)."""
    if hints.get("multi_channel_pass") is True:
        return True
    if hints.get("S1") is True or hints.get("S3") is True:
        return True
    # Two+ quality channels present (X + flow, X + curve, flow + curve, …)
    qs = [s for s in _sources(rec) if s in QUALITY_SOURCES]
    return len(set(qs)) >= 2


def has_publish_quality_evidence(rec: DiscoveryRecord, hints: Optional[dict[str, Any]] = None) -> bool:
    """True when at least one of: organic X, positive flow, multi-channel intel."""
    h = hints if hints is not None else _feature_hints(rec)
    return (
        _organic_x_evidence(rec, h)
        or _first_buyer_flow_positive(rec, h)
        or _multi_channel_intel_pass(rec, h)
    )


def _thin_boost_or_parasite_only(rec: DiscoveryRecord, hints: dict[str, Any]) -> Optional[str]:
    """Return reject reason if only thin dex / boost / parasite — no real evidence."""
    if hints.get("parasite_of_runner") is True or hints.get("parasite") is True:
        return "parasite_only"
    if hints.get("boost_only") is True or hints.get("paid_boost") is True:
        if not has_publish_quality_evidence(rec, hints):
            return "boost_only_no_organic"
    srcs = set(_sources(rec))
    non_lag = srcs - {"fomo_sidebar"}
    if hints.get("thin_dex_new") is True and not has_publish_quality_evidence(rec, hints):
        return "thin_dex_new_only"
    # Pure dexscreener_new (or empty after stripping lagging) with no quality overlay
    if non_lag and non_lag <= {"dexscreener_new"} and not has_publish_quality_evidence(rec, hints):
        return "thin_dex_new_only"
    return None


def score_discovery(
    rec: DiscoveryRecord,
    *,
    now: Optional[datetime] = None,
    enriched_liq_usd: Optional[float] = None,
) -> GateResult:
    """Apply hard/soft gates → pursue | watch | reject."""
    reasons: list[str] = []
    warnings: list[str] = []
    hints = _feature_hints(rec)
    liq = enriched_liq_usd if enriched_liq_usd is not None else rec.liquidity_usd

    # --- Hard rejects ---
    if not is_resolvable_ca(rec.ca, rec.chain):
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["no_resolvable_ca"],
            pursue_eligible=False,
        )

    if hints.get("D1") is True or hints.get("sybil") is True:
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["d1_sybil_bundled"],
            pursue_eligible=False,
        )

    if _farm_domain_hit(rec):
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["known_farm_domain"],
            pursue_eligible=False,
        )

    if _fomo_only(rec):
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["fomo_only_discovery"],
            warnings=["FOMO/0xbot sidebar is lagging confirmation — never sole pursue"],
            pursue_eligible=False,
        )

    if hints.get("ca_mismatch") is True:
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["ca_mismatch"],
            pursue_eligible=False,
        )

    if hints.get("D2") is True and hints.get("parasite_of_runner") is True:
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["parasite_of_known_runner"],
            pursue_eligible=False,
        )

    if hints.get("airdrop_farm") is True:
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["airdrop_farm"],
            pursue_eligible=False,
        )

    # Thin dex / boost / parasite alone → hard reject (never publish BUY)
    thin_reason = _thin_boost_or_parasite_only(rec, hints)
    if thin_reason:
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=[thin_reason],
            warnings=warnings + ["thin_boost_parasite_no_emit"],
            pursue_eligible=False,
        )

    mc = rec.mc_usd
    if mc is not None and mc > MC_USD_HARD_REJECT:
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=[f"mc_above_hard_ceiling:{mc}"],
            pursue_eligible=False,
        )

    # Dead micro after enrich
    if liq is not None and liq < MIN_LIQ_USD_AFTER_ENRICH and (mc is None or mc < 50_000):
        # Only reject if we actually enriched (caller passed liq) or liq known zero
        if enriched_liq_usd is not None or liq == 0:
            return GateResult(
                decision=CandidateDecision.reject,
                reasons=["dead_micro_zero_liq"],
                pursue_eligible=False,
            )

    # --- Soft prefer / watch ---
    age = _age_minutes(rec, now=now)
    pump = _is_pump_curve_context(rec)
    mc_cap = MC_USD_PUMP_CURVE_MAX if pump else MC_USD_DEX_NEW_MAX

    early_age = age is not None and age <= AGE_MINUTES_PURSUE_MAX
    early_mc = mc is not None and mc <= mc_cap
    mc_unknown = mc is None

    s1 = hints.get("S1")
    s3 = hints.get("S3")
    s2 = hints.get("S2")
    window = hints.get("window_type") or rec.confidence_hints.get("window_type")

    feature_boost = s1 is True or s3 is True
    feature_scored = isinstance(s1, bool) or isinstance(s3, bool)

    # S2 late challenger soft-reject when not frenzy-lane (align pursue_buy)
    if window != "frenzy-lane" and s2 == "late_challenger":
        return GateResult(
            decision=CandidateDecision.reject,
            reasons=["s2_late_challenger"],
            pursue_eligible=False,
        )

    if feature_scored and not feature_boost and s1 is False and s3 is False:
        warnings.append("S1/S3 both false — soft demote to watch")
        return GateResult(
            decision=CandidateDecision.watch,
            reasons=["features_present_but_s1_s3_false"],
            warnings=warnings,
            pursue_eligible=False,
        )

    # Pursue: early age + (early MC or unknown) + not FOMO-only (already checked)
    # Prefer S1/S3 when scored; if unscored, age+mc band is enough for v0 pursue
    pursue = False
    if early_age and (early_mc or mc_unknown):
        if not feature_scored or feature_boost:
            pursue = True
            reasons.append("early_age_and_mc_band")
            if feature_boost:
                reasons.append("s1_or_s3_true")
        else:
            warnings.append("early band but S1/S3 false")
    elif early_age and mc is not None and mc > mc_cap:
        warnings.append(f"age early but mc {mc} > band {mc_cap}")
    elif age is not None and age <= AGE_MINUTES_WATCH_MAX:
        reasons.append("watch_band_age")
    else:
        if age is None:
            warnings.append("age_unknown")
            # Unknown age + low MC can still watch
            if early_mc or mc_unknown:
                reasons.append("watch_unknown_age")
            else:
                return GateResult(
                    decision=CandidateDecision.reject,
                    reasons=["stale_or_unknown_high_mc"],
                    warnings=warnings,
                    pursue_eligible=False,
                )
        else:
            reasons.append("age_past_watch_band")
            return GateResult(
                decision=CandidateDecision.reject,
                reasons=reasons,
                warnings=warnings,
                pursue_eligible=False,
            )

    if pursue:
        # Require at least one non-lagging source (already implied by not fomo_only)
        if rec.lagging_universe and rec.first_source == "fomo_sidebar":
            # upgraded by later source but first was fomo — still ok if non-fomo present
            if "fomo_sidebar" in rec.sources and len(rec.sources) == 1:
                return GateResult(
                    decision=CandidateDecision.reject,
                    reasons=["fomo_only_discovery"],
                    pursue_eligible=False,
                )
        # Age+MC alone is NOT enough for a published BUY — need quality evidence
        quality = has_publish_quality_evidence(rec, hints)
        if not quality:
            reasons.append("age_mc_without_quality_evidence")
            warnings.append(
                "pursue research only — need organic X, positive flow, or multi-channel intel to emit BUY"
            )
            return GateResult(
                decision=CandidateDecision.pursue,
                reasons=reasons,
                warnings=warnings,
                pursue_eligible=False,
            )
        reasons.append("publish_quality_evidence")
        return GateResult(
            decision=CandidateDecision.pursue,
            reasons=reasons,
            warnings=warnings,
            pursue_eligible=True,
        )

    return GateResult(
        decision=CandidateDecision.watch,
        reasons=reasons or ["interesting_missing_flow_or_narrative"],
        warnings=warnings,
        pursue_eligible=False,
    )
