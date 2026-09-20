"""MCP server entrypoint for x_intel → 0xbot handoff.

Tools (documented; stdio JSON-RPC minimal server):
  - list_decisions(since?, min_confidence?, unexpired_only?)
  - get_decision(decision_id)
  - list_candidates(since?, decision?)
  - get_account_leaderboard()
  - ack_decision(decision_id, status)

No wallets, keys, swaps, or order placement.
All decisions carry do_not_execute_until_armed=true.

Run: python -m x_intel.mcp_server.server
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from x_intel import DO_NOT_EXECUTE_UNTIL_ARMED, EXPERIMENT_ID
from x_intel.ledger.store import CandidateLedger
from x_intel.schemas.models import is_decision_stale

TOOL_DOCS = {
    "list_decisions": {
        "description": "List decision intents (disarmed). Filter by min_confidence / unexpired_only.",
        "params": ["since?", "min_confidence?", "unexpired_only?"],
    },
    "get_decision": {
        "description": "Fetch one decision by decision_id; includes stale flag.",
        "params": ["decision_id"],
    },
    "list_candidates": {
        "description": "List logged candidates (pursue|watch|reject) from the ledger.",
        "params": ["since?", "decision?"],
    },
    "get_account_leaderboard": {
        "description": "Stub source-account leaderboard for experiment_id=xintel_v0.",
        "params": [],
    },
    "ack_decision": {
        "description": "Ack a decision: consumed | rejected_by_execution | expired.",
        "params": ["decision_id", "status", "note?"],
    },
}


class XIntelMCP:
    def __init__(self) -> None:
        self.ledger = CandidateLedger()

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": name, **meta, "do_not_execute_until_armed": True}
            for name, meta in TOOL_DOCS.items()
        ]

    def list_decisions(
        self,
        since: Optional[str] = None,
        min_confidence: float = 0.0,
        unexpired_only: bool = True,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        decs = self.ledger.list_decisions(
            unexpired_only=unexpired_only,
            min_confidence=min_confidence,
            now=now,
        )
        if since:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            filtered = []
            for d in decs:
                issued = d.issued_at if d.issued_at.tzinfo else d.issued_at.replace(tzinfo=timezone.utc)
                if issued >= since_dt:
                    filtered.append(d)
            decs = filtered
        return {
            "experiment_id": EXPERIMENT_ID,
            "do_not_execute_until_armed": DO_NOT_EXECUTE_UNTIL_ARMED,
            "decisions": [
                {**d.model_dump(mode="json"), "stale": is_decision_stale(d, now=now)}
                for d in decs
            ],
        }

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        d = self.ledger.get_decision(decision_id)
        if d is None:
            return {"error": "not_found", "decision_id": decision_id}
        now = datetime.now(timezone.utc)
        return {**d.model_dump(mode="json"), "stale": is_decision_stale(d, now=now)}

    def list_candidates(
        self,
        since: Optional[str] = None,
        decision: Optional[str] = None,
    ) -> dict[str, Any]:
        since_dt = None
        if since:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        cands = self.ledger.list_candidates(decision=decision, since=since_dt)
        return {
            "experiment_id": EXPERIMENT_ID,
            "count": len(cands),
            "candidates": [c.model_dump(mode="json") for c in cands],
        }

    def get_account_leaderboard(self) -> dict[str, Any]:
        return self.ledger.account_leaderboard_stub()

    def ack_decision(
        self,
        decision_id: str,
        status: str,
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        return self.ledger.ack_decision(decision_id, status, note)

    def dispatch(self, tool: str, args: dict[str, Any]) -> Any:
        if tool not in TOOL_DOCS:
            return {"error": "unknown_tool", "tool": tool, "available": list(TOOL_DOCS)}
        return getattr(self, tool)(**args)


def main() -> None:
    """Minimal CLI / stdio demo of MCP tool surface."""
    mcp = XIntelMCP()
    if len(sys.argv) > 1 and sys.argv[1] == "tools":
        print(json.dumps(mcp.list_tools(), indent=2))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "call":
        tool = sys.argv[2]
        args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        print(json.dumps(mcp.dispatch(tool, args), indent=2, default=str))
        return
    print(
        json.dumps(
            {
                "server": "x_intel.mcp_server",
                "experiment_id": EXPERIMENT_ID,
                "do_not_execute_until_armed": True,
                "tools": mcp.list_tools(),
                "usage": [
                    "python -m x_intel.mcp_server.server tools",
                    'python -m x_intel.mcp_server.server call list_candidates {}',
                    'python -m x_intel.mcp_server.server call list_decisions \'{"unexpired_only": true}\'',
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
