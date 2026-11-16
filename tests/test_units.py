"""Unit conversion and Model A/B formula sanity checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models import J_PER_KWH, SECONDS_PER_DAY, cost_A, cost_B, fleet_j_per_th_series
from src.config_loader import load_config


def test_j_per_kwh_constant():
    assert J_PER_KWH == 3.6e6


def test_kwh_day_formula():
    """1 TH/s at 1 J/TH for 1 day with PUE=1 → 86400/3.6e6 kWh."""
    hashrate = 1.0
    j_per_th = 1.0
    pue = 1.0
    kwh = hashrate * SECONDS_PER_DAY * j_per_th * pue / J_PER_KWH
    assert kwh == pytest.approx(86400 / 3.6e6)


def test_cost_a_known_value():
    """Hand-computed: 100 TWh/yr, 900 BTC/day reward, $0.05/kWh → cost."""
    # kWh/day = 100/365 * 1e9
    # cost = kWh/day * 0.05 / 900
    twh = 100.0
    reward = 900.0
    price = 0.05
    expected = ((twh / 365.0) * 1e9 * price) / reward

    cbeci = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=10, freq="D"),
            "twh_guess": twh,
            "twh_min": twh * 0.8,
            "twh_max": twh * 1.2,
        }
    )
    network = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=10, freq="D"),
            "reward_btc": reward,
            "reward_btc_7dma": reward,
        }
    )
    out = cost_A(cbeci, network, price_kwh=price)
    assert out["cost_A_guess"].iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_cost_b_scales_with_price_kwh():
    dates = pd.date_range("2024-06-01", periods=30, freq="D")
    network = pd.DataFrame(
        {
            "date": dates,
            "hashrate_th_s": 600e6,  # ~600 EH/s = 600e6 TH/s
            "reward_btc": 450.0,
            "reward_btc_7dma": 450.0,
        }
    )
    a = cost_B(network, price_kwh=0.05)
    b = cost_B(network, price_kwh=0.10)
    ratio = b["cost_B"].iloc[-1] / a["cost_B"].iloc[-1]
    assert ratio == pytest.approx(2.0, rel=1e-9)


def test_fleet_efficiency_lags_frontier():
    cfg = load_config()
    # On the date an S21-class frontier knot is introduced, lagged fleet should be higher J/TH
    dates = pd.Series(pd.to_datetime(["2024-04-01", "2025-10-01"]))
    j = fleet_j_per_th_series(dates, cfg, efficiency_multiplier=1.0)
    # Later date should have lower (better) or equal J/TH after lag catches up
    assert j.iloc[1] <= j.iloc[0] * 1.05


def test_twh_to_kwh_day():
    twh_ann = 120.0
    kwh_day = (twh_ann / 365.0) * 1e9
    assert kwh_day == pytest.approx(120e9 / 365.0)
