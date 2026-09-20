"""FastAPI app — intelligence handoff only. No wallets, keys, or order placement.

Run: uvicorn x_intel.api.main:app --reload
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from x_intel import EXPERIMENT_ID, __version__
from x_intel.config import do_not_execute_until_armed, is_armed
from x_intel.ledger.store import CandidateLedger
from x_intel.schemas.models import is_decision_stale

app = FastAPI(
    title="x_intel",
    description=(
        "Intelligence / decision layer for 0xbot. "
        "Emits intents only; arm via XINTEL_ARMED. "
        f"experiment_id={EXPERIMENT_ID}"
    ),
    version=__version__,
)

ledger = CandidateLedger()


class AckBody(BaseModel):
    decision_id: UUID
    status: str = Field(description="consumed | rejected_by_execution | expired")
    note: Optional[str] = None


@app.get("/v1/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "x_intel",
        "version": __version__,
        "experiment_id": EXPERIMENT_ID,
        "do_not_execute_until_armed": do_not_execute_until_armed(),
        "xintel_armed": is_armed(),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/v1/candidates")
def list_candidates(
    decision: Optional[str] = Query(None, description="pursue|watch|reject"),
    experiment_id: str = Query(EXPERIMENT_ID),
) -> dict[str, Any]:
    cands = ledger.list_candidates(decision=decision, experiment_id=experiment_id)
    return {
        "experiment_id": experiment_id,
        "count": len(cands),
        "candidates": [c.model_dump(mode="json") for c in cands],
    }


@app.get("/v1/candidates/{candidate_id}")
def get_candidate(candidate_id: UUID) -> dict[str, Any]:
    c = ledger.get_candidate(candidate_id)
    if c is None:
        raise HTTPException(404, "candidate not found")
    return c.model_dump(mode="json")


@app.get("/v1/decisions")
def list_decisions(
    unexpired: bool = Query(True, alias="unexpired"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    decs = ledger.list_decisions(
        unexpired_only=unexpired,
        min_confidence=min_confidence,
        now=now,
    )
    return {
        "experiment_id": EXPERIMENT_ID,
        "do_not_execute_until_armed": do_not_execute_until_armed(),
        "xintel_armed": is_armed(),
        "count": len(decs),
        "decisions": [
            {
                **d.model_dump(mode="json"),
                "stale": is_decision_stale(d, now=now),
            }
            for d in decs
        ],
    }


@app.get("/v1/decisions/{decision_id}")
def get_decision(decision_id: UUID) -> dict[str, Any]:
    d = ledger.get_decision(decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    now = datetime.now(timezone.utc)
    return {
        **d.model_dump(mode="json"),
        "stale": is_decision_stale(d, now=now),
    }


@app.post("/v1/ack")
def ack_decision(body: AckBody) -> dict[str, Any]:
    try:
        return ledger.ack_decision(body.decision_id, body.status, body.note)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/v1/leaderboard")
def leaderboard() -> dict[str, Any]:
    return ledger.account_leaderboard_stub()
