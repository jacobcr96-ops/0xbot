"""Decision emitters — intents only, never place trades."""

from x_intel.emit.pursue_buy import emit_pursue_buy, pursue_candidate_to_buy

__all__ = ["emit_pursue_buy", "pursue_candidate_to_buy"]
