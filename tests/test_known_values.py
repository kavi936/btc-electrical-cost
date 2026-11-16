"""Integration-ish checks against processed artifacts when present."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.config_loader import load_config, resolve_path
from src.models import cost_A

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    return load_config()


def test_model_a_formula_matches_manual():
    twh, reward, pk = 150.0, 460.0, 0.05
    expected = ((twh / 365.0) * 1e9 * pk) / reward
    cbeci = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=14, freq="D"),
            "twh_guess": twh,
            "twh_min": twh * 0.85,
            "twh_max": twh * 1.15,
        }
    )
    network = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=14, freq="D"),
            "reward_btc": reward,
            "reward_btc_7dma": reward,
        }
    )
    out = cost_A(cbeci, network, price_kwh=pk)
    assert out["cost_A_guess"].iloc[-1] == pytest.approx(expected)


@pytest.mark.skipif(
    not (ROOT / "data/processed/costs.parquet").exists(),
    reason="processed costs not built yet",
)
def test_live_model_a_in_sanity_band(cfg):
    costs = pd.read_parquet(resolve_path(cfg, "processed") / "costs.parquet")
    row = costs.dropna(subset=["cost_A_guess"]).iloc[-1]
    lo = cfg["tests"]["model_a_sanity_low"]
    hi = cfg["tests"]["model_a_sanity_high"]
    assert lo <= float(row["cost_A_guess"]) <= hi, (
        f"Model A guess ${row['cost_A_guess']:.0f} outside ${lo:.0f}–${hi:.0f}"
    )
