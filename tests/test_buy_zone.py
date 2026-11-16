"""Buy-zone band helpers and config sanity."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config_loader import load_config
from src.tests_claim import _episode_starts, buy_zone_analysis


def test_buy_zone_config_lengths():
    bz = load_config()["tests"]["buy_zone"]
    assert len(bz["band_labels"]) == len(bz["band_edges_pct"]) - 1
    assert bz["recommended_high_pct"] <= bz["stretch_high_pct"]
    assert bz["stretch_high_pct"] <= bz["fade_above_pct"]


def test_episode_starts_one_per_stretch():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=10, freq="D"),
            "close": 1.0,
        }
    )
    mask = pd.Series([False, True, True, False, True, True, True, False, False, True])
    starts = _episode_starts(df, mask)
    assert len(starts) == 3
    assert list(starts.index) == [1, 4, 9]


def test_buy_zone_analysis_smoke():
    dates = pd.date_range("2018-01-01", periods=800, freq="D")
    # Synthetic: cost flat 10k; price oscillates from below to 2x
    rng = np.random.default_rng(0)
    shock = np.cumsum(rng.normal(0, 0.02, size=len(dates)))
    close = 10000 * np.exp(shock)
    ohlc = pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "low_min": close * 0.99,
            "low_median": close * 0.99,
            "close": close,
            "volume": 1.0,
        }
    )
    costs = pd.DataFrame({"date": dates, "cost_A_guess_sma7": 10000.0})
    out = buy_zone_analysis(ohlc, costs)
    assert "bands" in out and "proximity" in out and "recommendation" in out
    assert not out["bands"].empty
    assert "narrative" in out["recommendation"]
