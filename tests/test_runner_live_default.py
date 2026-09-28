"""Runner CLI: live by default when armed; --offline/--no-live for deliberate offline runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from x_intel.discovery import runner as runner_mod
from x_intel.discovery.runner import resolve_live


def test_resolve_live_matrix(tmp_path: Path):
    assert resolve_live(armed=True) == (True, "default:armed")
    assert resolve_live(armed=False) == (False, "default:disarmed")
    assert resolve_live(offline_flag=True, armed=True) == (False, "flag:--offline")
    assert resolve_live(live_flag=True, armed=False) == (True, "flag:--live")
    assert resolve_live(fixture_dir=tmp_path, armed=True) == (False, "fixture_dir")
    assert resolve_live(fixture_dir=tmp_path, live_flag=True, armed=True)[0] is True
    with pytest.raises(ValueError):
        resolve_live(live_flag=True, offline_flag=True)


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run_cycle(**kw: Any) -> list:
        calls.append(kw)
        return []

    monkeypatch.setattr(runner_mod, "run_cycle", fake_run_cycle)
    return calls


@pytest.mark.parametrize(
    "argv,armed,expect",
    [
        (["once"], "true", True),
        (["once"], "false", False),
        (["once", "--live"], "false", True),
        (["once", "--offline"], "true", False),
        (["once", "--no-live"], "true", False),
    ],
)
def test_main_live_resolution(monkeypatch, caplog, tmp_path, argv, armed, expect):
    monkeypatch.setenv("XINTEL_ARMED", armed)
    calls = _capture(monkeypatch)
    caplog.set_level("INFO", logger="x_intel.discovery.runner")
    assert runner_mod.main(argv + ["--data-dir", str(tmp_path)]) == 0
    assert calls[0]["live"] is expect
    assert f"live={expect}" in caplog.text


def test_main_rejects_live_and_offline(monkeypatch, tmp_path):
    _capture(monkeypatch)
    with pytest.raises(SystemExit):
        runner_mod.main(["once", "--live", "--offline", "--data-dir", str(tmp_path)])
