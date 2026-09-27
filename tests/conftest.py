"""Shared test fixtures."""

from __future__ import annotations

import pytest

from x_intel.discovery import enrich


@pytest.fixture(autouse=True)
def _reset_jup_cooldown():
    """Jupiter 429 cooldown is process-global; never leak it across tests."""
    enrich.reset_jup_cooldown()
    yield
    enrich.reset_jup_cooldown()
