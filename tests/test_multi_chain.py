"""Multi-chain live quote, Dex EVM poll separation, identity guards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from x_intel.config import configured_chains, is_evm_chain
from x_intel.discovery.bus import DiscoveryBus, normalize_ca, record_key
from x_intel.discovery.enrich import looks_like_evm_ca, quote_mint, resolve_quote_chain
from x_intel.discovery.models import DiscoveryEvent
from x_intel.discovery.sources.dexscreener_new import DexScreenerNewSource, skip_dex_solana
from x_intel.discovery.watch_escalate import fetch_mc_usd
from x_intel.tools import live_quote as live_quote_mod


CROW_CA = "0x35abc2ec5d2b36cfedba65563443201618d6f1bf"


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    (data / "health").mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    return data


def test_configured_chains_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XINTEL_CHAINS", raising=False)
    assert "solana" in configured_chains() and "bsc" in configured_chains()
    monkeypatch.setenv("XINTEL_CHAINS", "solana,bsc,base")
    assert configured_chains() == frozenset({"solana", "bsc", "base"})


def test_looks_like_evm_and_resolve_chain():
    assert looks_like_evm_ca(CROW_CA)
    assert not looks_like_evm_ca("He5XcCaLrygisurxfDYH7vumKW5yopVZcA9DKPvpH4Lz")
    assert resolve_quote_chain(CROW_CA, "bsc") == "bsc"
    assert resolve_quote_chain(CROW_CA, "solana") == ""  # never solana
    assert resolve_quote_chain("He5XcCaLrygisurxfDYH7vumKW5yopVZcA9DKPvpH4Lz", None) == "solana"
    assert is_evm_chain("bsc")


def test_bus_evm_not_keyed_as_solana(tmp_data: Path):
    bus = DiscoveryBus(store_path=tmp_data / "discovery_bus.json")
    ev = DiscoveryEvent(
        source="dexscreener_new",
        discovered_at=datetime.now(timezone.utc),
        chain="solana",  # mis-tag
        ca=CROW_CA,
        ticker="CROW",
    )
    rec, first = bus.upsert(ev)
    assert first is True
    assert rec.chain != "solana"
    assert record_key(rec.chain, rec.ca) != record_key("solana", normalize_ca(CROW_CA))
    # Proper bsc key is distinct
    ev2 = DiscoveryEvent(
        source="dexscreener_new",
        discovered_at=datetime.now(timezone.utc),
        chain="bsc",
        ca=CROW_CA,
        ticker="CROW",
    )
    rec2, first2 = bus.upsert(ev2)
    assert first2 is True  # different chain key
    assert rec2.chain == "bsc"


def test_quote_mint_evm_skips_pump_uses_goplus(monkeypatch: pytest.MonkeyPatch):
    """When Dex fails, GoPlus metadata returns stale (no invented MC)."""

    def fake_http(url: str, *, timeout: float = 10.0) -> Any:
        if "gopluslabs" in url:
            return {
                "code": 1,
                "message": "OK",
                "result": {
                    CROW_CA.lower(): {
                        "token_name": "CROW",
                        "token_symbol": "CROW",
                        "is_in_dex": "1",
                        "holder_count": "678",
                        "total_supply": "960828949",
                    }
                },
            }
        raise OSError("dex down")

    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")  # must not block EVM
    with patch("x_intel.discovery.enrich._http_get_json", side_effect=fake_http):
        out = quote_mint(CROW_CA, chain="bsc", allow_dex=True)
    assert out.get("ticker") == "CROW"
    assert out.get("name") == "CROW"
    assert out.get("mc_usd") is None
    assert out.get("stale") is True
    assert out.get("ok") is False
    assert out.get("source") == "goplus"
    assert out.get("is_in_dex") is True
    # pump never called for EVM — no pump error required, just no pump source
    assert "pump.fun" not in (out.get("source") or "")


def test_quote_mint_evm_dex_live_mc(monkeypatch: pytest.MonkeyPatch):
    pair = {
        "chainId": "bsc",
        "marketCap": 123456.0,
        "fdv": 123456.0,
        "priceUsd": "0.001",
        "liquidity": {"usd": 50000},
        "volume": {"h24": 1000},
        "baseToken": {"address": CROW_CA, "symbol": "CROW", "name": "CROW"},
        "info": {"socials": []},
    }

    def fake_http(url: str, *, timeout: float = 10.0) -> Any:
        if "dexscreener" in url:
            if "/tokens/v1/" in url:
                return [pair]
            return {"pairs": [pair]}
        raise AssertionError(f"unexpected url {url}")

    with patch("x_intel.discovery.enrich._http_get_json", side_effect=fake_http):
        out = quote_mint(CROW_CA, chain="bsc", allow_dex=True)
    assert out["ok"] is True
    assert out["mc_usd"] == 123456.0
    assert out["stale"] is False
    assert out["source"] == "dexscreener"
    assert out["chain"] == "bsc"


def test_skip_dex_solana_still_polls_evm(tmp_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    monkeypatch.setenv("XINTEL_CHAINS", "solana,bsc,base")
    monkeypatch.setenv("XINTEL_DEX_EVM_INTERVAL_SEC", "1")
    assert skip_dex_solana() is True

    profiles = [
        {"chainId": "solana", "tokenAddress": "So11111111111111111111111111111111111111112", "symbol": "SOL"},
        {"chainId": "bsc", "tokenAddress": CROW_CA, "symbol": "CROW"},
        {"chainId": "base", "tokenAddress": "0xabcdeffedcbaabcdeffedcbaabcdeffedcbaabcd", "symbol": "BASEY"},
    ]

    src = DexScreenerNewSource(
        live=True,
        chains={"solana", "bsc", "base"},
        data_dir_path=tmp_data,
        include_bsc_new_pairs=False,
    )

    def fake_http(url: str) -> Any:
        if "token-profiles" in url or "token-boosts" in url:
            return profiles
        raise AssertionError(url)

    with patch.object(src, "_http_json", side_effect=fake_http):
        events = src.poll()
    chains = {e.chain for e in events}
    assert "solana" not in chains  # SKIP_DEX
    assert "bsc" in chains
    assert "base" in chains
    assert all(not (e.chain == "solana" and e.ca.lower().startswith("0x")) for e in events)


def test_fetch_mc_usd_evm_ignores_skip_dex(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")

    def boom_pump(*a, **k):
        raise AssertionError("pump should not run for EVM")

    with patch("x_intel.discovery.watch_escalate._fetch_pumpfun_mc", side_effect=boom_pump):
        with patch(
            "x_intel.discovery.watch_escalate._fetch_dex_mc",
            return_value=42_000.0,
        ) as dex:
            mc = fetch_mc_usd(CROW_CA, chain="bsc")
    assert mc == 42_000.0
    dex.assert_called_once()


def test_live_quote_cli_requires_chain_for_evm(capsys):
    rc = live_quote_mod.main(["--ca", CROW_CA])
    assert rc == 1
    err = capsys.readouterr().err
    assert "requires --chain" in err


def test_live_quote_cli_crow_goplus(monkeypatch: pytest.MonkeyPatch, capsys):
    def fake_quote(mint, *, chain=None, timeout=10.0, allow_dex=None):
        return {
            "ok": False,
            "mint": mint,
            "ca": mint,
            "chain": chain,
            "ticker": "CROW",
            "name": "CROW",
            "mc_usd": None,
            "source": "goplus",
            "stale": True,
            "is_in_dex": True,
            "fetched_at": "2026-09-20T00:00:00+00:00",
            "age_sec": 0,
        }

    with patch("x_intel.tools.live_quote.quote_mint", side_effect=fake_quote):
        rc = live_quote_mod.main(["--chain", "bsc", "--ca", CROW_CA])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["mc_status"] == "STALE"
    assert out["ticker"] == "CROW"


def test_fixture_respects_xintel_chains(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_CHAINS", "bsc")
    fix = Path(__file__).resolve().parent / "fixtures" / "discovery"
    src = DexScreenerNewSource(fixture_dir=fix, live=False, chains=set(configured_chains()))
    events = src.poll()
    # fixture has solana+base only — with chains={bsc} expect empty
    assert events == []
