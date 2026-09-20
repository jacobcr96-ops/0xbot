"""Outcome return math tests."""

import pytest

from x_intel.ledger.outcomes import compute_extrema, compute_fractional_return


def test_fractional_return_positive():
    assert compute_fractional_return(100.0, 125.0) == pytest.approx(0.25)


def test_fractional_return_negative():
    assert compute_fractional_return(1.0, 0.6) == pytest.approx(-0.4)


def test_fractional_return_invalid_baseline():
    assert compute_fractional_return(0.0, 1.0) is None
    assert compute_fractional_return(-1.0, 1.0) is None


def test_extrema():
    runup, dd = compute_extrema(10.0, [11.0, 15.0, 8.0, 12.0])
    assert runup == pytest.approx(0.5)
    assert dd == pytest.approx(-0.2)


def test_extrema_empty():
    assert compute_extrema(10.0, []) == (None, None)
