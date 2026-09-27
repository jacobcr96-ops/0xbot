"""publish_decisions_git.sh must never ship cancelled/expired decisions (dead_coin_floor)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "publish_decisions_git.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None, reason="needs git + jq"
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def test_publisher_skips_cancelled_and_filters_inbox(tmp_path: Path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    (work / "data" / "decisions").mkdir(parents=True)
    shutil.copy2(SCRIPT, work / "scripts" / "publish_decisions_git.sh")
    _git(work, "init", "-q")
    _git(work, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    _git(work, "remote", "add", "origin", str(remote))

    d = work / "data" / "decisions"
    live = {"decision_id": "live-1", "action": "BUY", "risk_flags": ["watch_dip_buy"]}
    dead = {
        "decision_id": "dead-1",
        "action": "BUY",
        "risk_flags": ["watch_dip_buy"],
        "status": "cancelled",
        "cancel_reason": "dead_coin_floor",
        "do_not_execute_until_armed": True,
    }
    dead2 = {"decision_id": "dead-2", "action": "BUY", "do_not_publish": True}
    for obj in (live, dead, dead2):
        (d / f"{obj['decision_id']}.json").write_text(json.dumps(obj))
    (d / "inbox.jsonl").write_text(
        "".join(json.dumps({"decision_id": i}) + "\n" for i in ("live-1", "dead-1", "dead-2"))
    )

    out = subprocess.run(
        ["bash", "scripts/publish_decisions_git.sh"],
        cwd=work, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
             "XINTEL_SIGNALS_REMOTE": "origin", "XINTEL_SIGNALS_BRANCH": "xintel/signals"},
    )
    assert out.returncode == 0, out.stderr + out.stdout
    assert "skip cancelled/expired decision" in out.stdout

    files = _git(remote, "ls-tree", "-r", "--name-only", "xintel/signals").split()
    assert "data/decisions/live-1.json" in files
    assert "data/decisions/dead-1.json" not in files
    assert "data/decisions/dead-2.json" not in files
    inbox = _git(remote, "show", "xintel/signals:data/decisions/inbox.jsonl")
    assert "live-1" in inbox and "dead-1" not in inbox and "dead-2" not in inbox
