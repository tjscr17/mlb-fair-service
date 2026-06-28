"""Webapp endpoint smoke tests (mock mode — no network).

Guards the demo API surface: these would have caught the `/api/slate` 500 and the
stale-route 404 that slipped through earlier. Live mode hits real APIs and is not
exercised here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webapp.app import app

client = TestClient(app)


def test_slate_mock():
    r = client.get("/api/slate?mode=mock")
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "mock"
    assert d["spine_count"] == 6
    assert d["bound_count"] == 6
    assert d["quotable_count"] == 5  # the suspended game is bound but not quotable


def test_game_detail_mock():
    r = client.get("/api/game/778001?mode=mock")
    assert r.status_code == 200
    d = r.json()
    assert d["home_team"] == "Boston Red Sox"
    assert len(d["books"]) == 4
    assert d["source_book"] == "pinnacle"
    assert {m["side"] for m in d["kalshi_markets"]} == {"home", "away"}
    assert d["links"]["mlb_gameday"].endswith("/778001")
    # fair vs Kalshi YES price (the edge)
    contracts = {m["side"]: m for m in d["kalshi_markets"]}
    assert contracts["home"]["yes_price"] == pytest.approx(0.57)  # mid of 0.56/0.58
    assert contracts["away"]["yes_price"] == pytest.approx(0.43)  # mid of 0.42/0.44
    # BOS (home) fair ~0.58 > 0.57 market -> positive edge; NYY (away) the mirror
    assert contracts["home"]["edge"] > 0 and contracts["away"]["edge"] < 0


def test_game_detail_unknown_is_404():
    assert client.get("/api/game/999999?mode=mock").status_code == 404


def test_devig_endpoint():
    d = client.get("/api/devig?home=-150&away=130").json()
    assert abs(d["home"] + d["away"] - 1.0) < 1e-9


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "MLB Fair-Value" in r.text
