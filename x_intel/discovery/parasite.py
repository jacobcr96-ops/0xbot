"""Parasite / clone detection keyed by CA, not ticker alone.

STAMP parasite = ticker/name mimics STAMP but mint ≠ EKtmPPLa… (actual STAMP).
Do NOT reject Shielded Cat (SCAT) just for letter overlap with STAMP.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from x_intel.discovery.bus import normalize_ca

# Canonical runner tickers → true mint/CA. Clones that mimic name/ticker with a
# different CA are parasites.
KNOWN_RUNNER_CAS: dict[str, str] = {
    "STAMP": "EKtmPPLaCbEEKiwoHHtV7TsRsmPXs5CMGtQtZFSiinsc",
}

def _identity_blob(rec: Any) -> str:
    parts: list[str] = []
    for attr in ("ticker", "symbol", "name"):
        v = getattr(rec, attr, None)
        if v:
            parts.append(str(v))
    hints = getattr(rec, "confidence_hints", None) or {}
    if isinstance(hints, dict):
        for k in ("name", "symbol", "ticker"):
            if hints.get(k):
                parts.append(str(hints[k]))
    return " ".join(parts)


def mimics_known_runner(blob: str, known_ticker: str) -> bool:
    """True when name/ticker mimics known_ticker (prefix/clone), not loose substring.

    SCAT does not mimic STAMP. STAMPDOG / Stamp Pepe / STAMPXMR do.
    """
    if not blob or not known_ticker:
        return False
    kt = known_ticker.upper()
    tokens = re.findall(r"[A-Za-z0-9]+", blob.upper())
    for t in tokens:
        if t == kt or (len(t) > len(kt) and t.startswith(kt)):
            return True
    return False


def detect_parasite_by_ca(rec: Any) -> Optional[str]:
    """Return reject reason if rec is a name-clone of a known runner CA.

    Authentic known runner (matching CA) → None (not a parasite).
    """
    ca = normalize_ca(getattr(rec, "ca", "") or "")
    if not ca:
        return None
    blob = _identity_blob(rec)
    for ticker, known_ca in KNOWN_RUNNER_CAS.items():
        known_n = normalize_ca(known_ca)
        if ca == known_n:
            return None
        if mimics_known_runner(blob, ticker):
            return "name_parasite_heuristic"
    return None


def annotate_parasite_hints(rec: Any) -> Any:
    """Set confidence_hints parasite flags when CA-keyed clone detected."""
    reason = detect_parasite_by_ca(rec)
    hints = dict(getattr(rec, "confidence_hints", None) or {})
    if reason:
        hints["parasite"] = True
        hints["parasite_of_runner"] = True
        hints["name_parasite_heuristic"] = True
        hints["D2"] = True
        # Which runner?
        blob = _identity_blob(rec)
        for ticker, known_ca in KNOWN_RUNNER_CAS.items():
            if mimics_known_runner(blob, ticker):
                hints["parasite_of"] = ticker
                hints["parasite_canonical_ca"] = known_ca
                break
    else:
        # Clear false ticker-only flags if authentic or unrelated
        ca = normalize_ca(getattr(rec, "ca", "") or "")
        for ticker, known_ca in KNOWN_RUNNER_CAS.items():
            if ca == normalize_ca(known_ca):
                hints["authentic_runner"] = ticker
                hints.pop("parasite", None)
                hints.pop("parasite_of_runner", None)
                hints.pop("name_parasite_heuristic", None)
    rec.confidence_hints = hints
    return rec


__all__ = [
    "KNOWN_RUNNER_CAS",
    "detect_parasite_by_ca",
    "annotate_parasite_hints",
    "mimics_known_runner",
]
