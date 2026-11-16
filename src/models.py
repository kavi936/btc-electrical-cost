"""Electrical / production cost models for Bitcoin.

Model A — CCAF / Capriole style from CBECI annualised TWh and miner rewards.
Model B — Bottom-up from hashrate × fleet J/TH × PUE (independent of CBECI).
Model C — Optional Capriole TradingView export.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.config_loader import load_config, resolve_path
from src.fetch_utils import save_parquet, setup_logging

logger = logging.getLogger(__name__)

# Unit conversion: J → kWh. 1 kWh = 3.6e6 J
J_PER_KWH = 3.6e6
SECONDS_PER_DAY = 86400.0


def fleet_j_per_th_series(
    dates: pd.Series,
    cfg: dict[str, Any] | None = None,
    *,
    efficiency_multiplier: float = 1.0,
) -> pd.Series:
    """Piecewise-linear fleet average J/TH, lagging the frontier curve.

    Why lag: installed fleet trails state-of-the-art ASICs; config sets
    frontier_lag_months (default 18). Multiplier scales the whole curve
    for sensitivity analysis.
    """
    cfg = cfg or load_config()
    frontier = cfg["efficiency"]["frontier_j_per_th"]
    lag_months = int(cfg["efficiency"]["frontier_lag_months"])

    knot_dates = pd.to_datetime([r["date"] for r in frontier])
    knot_vals = np.array([float(r["j_per_th"]) for r in frontier], dtype=float)

    # Lag frontier dates forward in calendar time = fleet still on older hardware
    fleet_knot_dates = knot_dates + pd.DateOffset(months=lag_months)

    x = pd.to_datetime(dates).astype("int64").to_numpy(dtype=float)
    xp = fleet_knot_dates.astype("int64").to_numpy(dtype=float)
    # np.interp needs monotonic xp
    order = np.argsort(xp)
    xp, yp = xp[order], knot_vals[order]
    interp = np.interp(x, xp, yp, left=yp[0], right=yp[-1])
    return pd.Series(interp * efficiency_multiplier, index=dates.index, name="fleet_j_per_th")


def cost_A(
    cbeci: pd.DataFrame,
    network: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
    *,
    price_kwh: float | None = None,
) -> pd.DataFrame:
    """CCAF-style: (TWh_ann/365 * 1e9 * $/kWh) / reward_btc_7dma.

    Produces guess/min/max electrical cost and Capriole-style production cost
    (electrical / electricity_share).
    """
    cfg = cfg or load_config()
    price = float(price_kwh if price_kwh is not None else cfg["electricity"]["price_kwh"])
    share = float(cfg["electricity"]["electricity_share"])
    ma = int(cfg["smoothing"]["cost_ma_days"])
    ma21 = int(cfg["smoothing"]["cost_ma_days_optional"])

    c = cbeci[["date", "twh_guess", "twh_min", "twh_max"]].copy()
    c["date"] = pd.to_datetime(c["date"]).dt.normalize()
    n = network[["date", "reward_btc", "reward_btc_7dma"]].copy()
    n["date"] = pd.to_datetime(n["date"]).dt.normalize()

    df = c.merge(n, on="date", how="inner")
    reward = df["reward_btc_7dma"].replace(0, np.nan)

    def _line(twh: pd.Series) -> pd.Series:
        # TWh/yr → kWh/day: TWh/365 * 1e9
        kwh_day = (twh / 365.0) * 1e9
        return (kwh_day * price) / reward

    df["cost_A_guess"] = _line(df["twh_guess"])
    df["cost_A_min"] = _line(df["twh_min"])
    df["cost_A_max"] = _line(df["twh_max"])
    # Capriole "Production Cost" widens electrical cost by non-electric opex share
    df["cost_A_production"] = df["cost_A_guess"] / share

    for col in ("cost_A_guess", "cost_A_min", "cost_A_max", "cost_A_production"):
        df[f"{col}_sma7"] = df[col].rolling(ma, min_periods=1).mean()
        df[f"{col}_sma21"] = df[col].rolling(ma21, min_periods=1).mean()

    df["price_kwh_used"] = price
    df["model"] = "A"
    return df


def cost_B(
    network: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
    *,
    price_kwh: float | None = None,
    efficiency_multiplier: float = 1.0,
    pue: float | None = None,
) -> pd.DataFrame:
    """Bottom-up: hashrate × fleet J/TH × PUE → kWh/day → $/BTC.

    Independent of CBECI — useful cross-check of Model A.
    """
    cfg = cfg or load_config()
    price = float(price_kwh if price_kwh is not None else cfg["electricity"]["price_kwh"])
    pue_val = float(pue if pue is not None else cfg["pue"])
    ma = int(cfg["smoothing"]["cost_ma_days"])
    ma21 = int(cfg["smoothing"]["cost_ma_days_optional"])
    share = float(cfg["electricity"]["electricity_share"])

    df = network.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    if "hashrate_th_s" not in df.columns:
        raise ValueError("network frame missing hashrate_th_s for Model B")

    df["fleet_j_per_th"] = fleet_j_per_th_series(
        df["date"], cfg, efficiency_multiplier=efficiency_multiplier
    )

    # kWh/day = TH/s * 86400 s/day * J/TH * PUE / 3.6e6 J/kWh
    df["kwh_day"] = (
        df["hashrate_th_s"]
        * SECONDS_PER_DAY
        * df["fleet_j_per_th"]
        * pue_val
        / J_PER_KWH
    )
    reward = df["reward_btc_7dma"].replace(0, np.nan)
    df["cost_B"] = (df["kwh_day"] * price) / reward
    df["cost_B_production"] = df["cost_B"] / share
    df["cost_B_sma7"] = df["cost_B"].rolling(ma, min_periods=1).mean()
    df["cost_B_sma21"] = df["cost_B"].rolling(ma21, min_periods=1).mean()
    df["price_kwh_used"] = price
    df["efficiency_multiplier"] = efficiency_multiplier
    df["pue_used"] = pue_val
    df["model"] = "B"
    return df


def cost_C(cfg: dict[str, Any] | None = None) -> pd.DataFrame | None:
    """Optional Capriole series from TradingView CSV export."""
    cfg = cfg or load_config()
    path = resolve_path(cfg, "manual") / "capriole.csv"
    if not path.exists():
        logger.info("Model C skipped — no %s", path)
        return None

    df = pd.read_csv(path)
    # TradingView exports vary; try common column names
    date_col = next((c for c in df.columns if c.lower() in ("time", "date", "datetime")), df.columns[0])
    val_col = next(
        (c for c in df.columns if "cost" in c.lower() or "production" in c.lower() or c.lower() == "close"),
        df.columns[-1],
    )
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df[date_col], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize(),
            "cost_C": pd.to_numeric(df[val_col], errors="coerce"),
        }
    ).dropna()
    out["model"] = "C"
    logger.info("Loaded Capriole Model C: %d rows from %s", len(out), path)
    return out


def build_all_costs(
    cbeci: pd.DataFrame,
    network: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Merge Models A/B/(C) onto a common daily index for analysis."""
    cfg = cfg or load_config()
    a = cost_A(cbeci, network, cfg)
    b = cost_B(network, cfg)

    keep_a = [
        "date",
        "cost_A_guess",
        "cost_A_min",
        "cost_A_max",
        "cost_A_production",
        "cost_A_guess_sma7",
        "cost_A_guess_sma21",
        "twh_guess",
        "reward_btc_7dma",
    ]
    keep_b = [
        "date",
        "cost_B",
        "cost_B_production",
        "cost_B_sma7",
        "cost_B_sma21",
        "fleet_j_per_th",
        "kwh_day",
        "hashrate_th_s",
    ]
    merged = a[keep_a].merge(b[keep_b], on="date", how="outer")

    c = cost_C(cfg)
    if c is not None:
        merged = merged.merge(c[["date", "cost_C"]], on="date", how="left")

    merged = merged.sort_values("date").reset_index(drop=True)

    processed = resolve_path(cfg, "processed")
    save_parquet(merged, processed / "costs.parquet")
    return merged


def model_a_current_estimate(
    cbeci: pd.DataFrame,
    network: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
    *,
    price_kwh: float | None = None,
) -> dict[str, float]:
    """Latest Model A point estimate for sanity checks / current-state panel."""
    df = cost_A(cbeci, network, cfg, price_kwh=price_kwh).dropna(subset=["cost_A_guess"])
    if df.empty:
        raise ValueError("Model A produced no valid rows")
    row = df.iloc[-1]
    return {
        "date": row["date"],
        "cost_A_guess": float(row["cost_A_guess"]),
        "cost_A_min": float(row["cost_A_min"]),
        "cost_A_max": float(row["cost_A_max"]),
        "cost_A_production": float(row["cost_A_production"]),
        "twh_guess": float(row["twh_guess"]),
        "reward_btc_7dma": float(row["reward_btc_7dma"]),
        "price_kwh": float(row["price_kwh_used"]),
    }


if __name__ == "__main__":
    setup_logging()
    from src.fetch_cbeci import fetch_cbeci
    from src.fetch_coinmetrics import fetch_coinmetrics

    cfg = load_config()
    net = fetch_coinmetrics(cfg)
    cb = fetch_cbeci(cfg)
    costs = build_all_costs(cb, net, cfg)
    print(costs.tail())
    print(model_a_current_estimate(cb, net, cfg))
