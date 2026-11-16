"""Falsification tests for 'Bitcoin NEVER trades below electrical cost'."""

from __future__ import annotations

import logging
from typing import Any, Literal

import numpy as np
import pandas as pd

from src.config_loader import load_config, resolve_path
from src.fetch_utils import save_parquet, setup_logging
from src.models import cost_A, cost_B

logger = logging.getLogger(__name__)

PriceCol = Literal["low_min", "low_median", "close"]


def _align(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    cost_col: str,
) -> pd.DataFrame:
    o = ohlc.copy()
    o["date"] = pd.to_datetime(o["date"]).dt.normalize()
    c = costs[["date", cost_col]].copy()
    c["date"] = pd.to_datetime(c["date"]).dt.normalize()
    df = o.merge(c, on="date", how="inner").dropna(subset=[cost_col, "close"])
    return df.sort_values("date").reset_index(drop=True)


def breach_summary(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    cost_col: str,
    price_col: PriceCol = "low_median",
) -> dict[str, Any]:
    """Literal falsification: count days where price < cost."""
    df = _align(ohlc, costs, cost_col)
    if price_col not in df.columns:
        raise KeyError(price_col)

    below = df[price_col] < df[cost_col]
    close_below = df["close"] < df[cost_col]

    # Weekly close: resample to W-SUN
    weekly = (
        df.set_index("date")
        .resample("W-SUN")
        .agg({price_col: "last", "close": "last", cost_col: "last"})
        .dropna()
    )
    weekly_below = weekly["close"] < weekly[cost_col]

    first = df.loc[below, "date"].min() if below.any() else None
    first_close = df.loc[close_below, "date"].min() if close_below.any() else None

    claim_holds = not bool(below.any())
    return {
        "cost_col": cost_col,
        "price_col": price_col,
        "n_days": int(len(df)),
        "breach_days_low": int(below.sum()),
        "breach_pct_low": float(below.mean() * 100) if len(df) else 0.0,
        "breach_days_close": int(close_below.sum()),
        "breach_pct_close": float(close_below.mean() * 100) if len(df) else 0.0,
        "breach_weeks_close": int(weekly_below.sum()),
        "first_breach_date": None if pd.isna(first) else pd.Timestamp(first).date().isoformat(),
        "first_close_breach_date": None
        if pd.isna(first_close)
        else pd.Timestamp(first_close).date().isoformat(),
        "claim_never_holds": claim_holds,
        "start": df["date"].min().date().isoformat() if len(df) else None,
        "end": df["date"].max().date().isoformat() if len(df) else None,
    }


def episode_table(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    cost_col: str,
    price_col: PriceCol = "low_median",
    cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Group consecutive breach days into episodes with forward returns."""
    cfg = cfg or load_config()
    horizons = cfg["tests"]["forward_horizons"]
    df = _align(ohlc, costs, cost_col)
    df["below"] = df[price_col] < df[cost_col]
    df["pct_below"] = (df[cost_col] - df[price_col]) / df[cost_col] * 100.0

    # Episode id: increment when a True streak starts
    df["episode_id"] = (df["below"] & ~df["below"].shift(1, fill_value=False)).cumsum()
    df.loc[~df["below"], "episode_id"] = np.nan

    episodes: list[dict[str, Any]] = []
    for eid, g in df.dropna(subset=["episode_id"]).groupby("episode_id"):
        start = g["date"].iloc[0]
        end = g["date"].iloc[-1]
        max_idx = g["pct_below"].idxmax()
        max_row = g.loc[max_idx]

        # Days until close recovers above cost
        after = df[df["date"] > end]
        recovered = after[after["close"] > after[cost_col]]
        days_to_recover = int((recovered["date"].iloc[0] - end).days) if len(recovered) else None

        ep: dict[str, Any] = {
            "episode_id": int(eid),
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "duration_days": int((end - start).days) + 1,
            "max_pct_below": float(max_row["pct_below"]),
            "max_pct_below_date": max_row["date"].date().isoformat(),
            "price_at_max": float(max_row[price_col]),
            "cost_at_max": float(max_row[cost_col]),
            "days_until_close_above_cost": days_to_recover,
        }

        # Forward returns from episode start close
        start_close = float(g["close"].iloc[0])
        for h in horizons:
            future = df[df["date"] == start + pd.Timedelta(days=h)]
            if len(future):
                fut_close = float(future["close"].iloc[0])
                ep[f"ret_{h}d"] = (fut_close / start_close - 1.0) * 100.0
                ep[f"price_{h}d"] = fut_close
            else:
                # nearest available forward date within 3 days
                window = df[
                    (df["date"] > start + pd.Timedelta(days=h - 3))
                    & (df["date"] <= start + pd.Timedelta(days=h + 3))
                ]
                if len(window):
                    fut_close = float(window.iloc[-1]["close"])
                    ep[f"ret_{h}d"] = (fut_close / start_close - 1.0) * 100.0
                    ep[f"price_{h}d"] = fut_close
                else:
                    ep[f"ret_{h}d"] = np.nan
                    ep[f"price_{h}d"] = np.nan
        episodes.append(ep)

    return pd.DataFrame(episodes)


def sensitivity_grid(
    ohlc: pd.DataFrame,
    cbeci: pd.DataFrame,
    network: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
    *,
    price_col: PriceCol = "low_median",
    model: Literal["A", "B"] = "A",
) -> pd.DataFrame:
    """Grid over $/kWh × efficiency multiplier → breach-day counts."""
    cfg = cfg or load_config()
    prices = cfg["electricity"]["price_kwh_grid"]
    mults = cfg["efficiency"]["multiplier_grid"] if model == "B" else [1.0]

    rows: list[dict[str, Any]] = []
    for pk in prices:
        for mult in mults:
            if model == "A":
                costs = cost_A(cbeci, network, cfg, price_kwh=pk)
                cost_col = "cost_A_guess_sma7"
            else:
                costs = cost_B(network, cfg, price_kwh=pk, efficiency_multiplier=mult)
                cost_col = "cost_B_sma7"
            summary = breach_summary(ohlc, costs, cost_col, price_col=price_col)
            rows.append(
                {
                    "model": model,
                    "price_kwh": pk,
                    "efficiency_multiplier": mult,
                    "breach_days": summary["breach_days_low"],
                    "breach_pct": summary["breach_pct_low"],
                    "n_days": summary["n_days"],
                    "claim_never_holds": summary["claim_never_holds"],
                    "first_breach": summary["first_breach_date"],
                }
            )
    return pd.DataFrame(rows)


def forward_returns_signal(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    cost_col: str,
    cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Tradeable version: close within X% of cost (or below) vs unconditional."""
    cfg = cfg or load_config()
    bands = cfg["tests"]["proximity_bands"]
    horizons = [h for h in cfg["tests"]["forward_horizons"] if h >= 90]

    df = _align(ohlc, costs, cost_col)
    df["ratio"] = df["close"] / df[cost_col]

    results: list[dict[str, Any]] = []

    def _fwd_stats(mask: pd.Series, label: str) -> None:
        sub = df[mask].copy()
        n = len(sub)
        for h in horizons:
            rets = []
            max_dds = []
            for _, row in sub.iterrows():
                start = row["date"]
                start_px = row["close"]
                future = df[(df["date"] > start) & (df["date"] <= start + pd.Timedelta(days=h))]
                if future.empty:
                    continue
                end_px = float(future.iloc[-1]["close"])
                ret = end_px / start_px - 1.0
                rets.append(ret)
                # Max drawdown within window from start_px
                dd = (future["close"].min() / start_px) - 1.0
                max_dds.append(dd)
            if not rets:
                results.append(
                    {
                        "signal": label,
                        "horizon": h,
                        "n": 0,
                        "mean_ret_pct": np.nan,
                        "median_ret_pct": np.nan,
                        "hit_rate": np.nan,
                        "mean_max_dd_pct": np.nan,
                    }
                )
                continue
            arr = np.array(rets)
            results.append(
                {
                    "signal": label,
                    "horizon": h,
                    "n": int(len(arr)),
                    "mean_ret_pct": float(arr.mean() * 100),
                    "median_ret_pct": float(np.median(arr) * 100),
                    "hit_rate": float((arr > 0).mean() * 100),
                    "mean_max_dd_pct": float(np.mean(max_dds) * 100),
                }
            )

    # Unconditional: every day in sample
    _fwd_stats(pd.Series(True, index=df.index), "unconditional")
    _fwd_stats(df["close"] < df[cost_col], "below_cost")
    for band in bands:
        _fwd_stats(df["ratio"] <= (1.0 + band), f"within_{int(band * 100)}pct")

    return pd.DataFrame(results)


def ratio_analysis(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    cost_col: str,
) -> dict[str, Any]:
    """price/cost over time with percentile of current reading."""
    df = _align(ohlc, costs, cost_col)
    df["ratio"] = df["close"] / df[cost_col]
    current = float(df["ratio"].iloc[-1])
    pct = float((df["ratio"] < current).mean() * 100)
    return {
        "cost_col": cost_col,
        "current_date": df["date"].iloc[-1].date().isoformat(),
        "current_price": float(df["close"].iloc[-1]),
        "current_cost": float(df[cost_col].iloc[-1]),
        "current_ratio": current,
        "percentile": pct,
        "ratio_p5": float(df["ratio"].quantile(0.05)),
        "ratio_p25": float(df["ratio"].quantile(0.25)),
        "ratio_p50": float(df["ratio"].quantile(0.50)),
        "ratio_p75": float(df["ratio"].quantile(0.75)),
        "ratio_p95": float(df["ratio"].quantile(0.95)),
        "series": df[["date", "close", cost_col, "ratio"]],
    }


def current_state(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    cbeci: pd.DataFrame,
    network: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Today's cost from each model, distance to price, $55K claim check."""
    cfg = cfg or load_config()
    claimed = float(cfg["tests"]["claimed_current_cost"])
    last_px_row = ohlc.dropna(subset=["close"]).iloc[-1]
    price = float(last_px_row["close"])
    px_date = pd.Timestamp(last_px_row["date"]).date().isoformat()

    out: dict[str, Any] = {"price": price, "price_date": px_date, "models": {}}

    for col, label in (
        ("cost_A_guess_sma7", "A_guess_sma7"),
        ("cost_A_production", "A_production"),
        ("cost_B_sma7", "B_sma7"),
        ("cost_C", "C_capriole"),
    ):
        if col not in costs.columns:
            continue
        sub = costs.dropna(subset=[col])
        if sub.empty:
            continue
        row = sub.iloc[-1]
        cost = float(row[col])
        out["models"][label] = {
            "date": pd.Timestamp(row["date"]).date().isoformat(),
            "cost": cost,
            "pct_above_cost": (price / cost - 1.0) * 100.0,
        }

    # At what $/kWh does Model A equal the claimed $55K?
    base = cost_A(cbeci, network, cfg, price_kwh=0.05).dropna(subset=["cost_A_guess"])
    if not base.empty:
        latest = base.iloc[-1]
        # cost scales linearly with price_kwh
        cost_at_05 = float(latest["cost_A_guess"])
        implied_kwh = claimed * 0.05 / cost_at_05 if cost_at_05 else np.nan
        out["claimed_55k"] = {
            "claimed": claimed,
            "model_A_guess_at_0.05": cost_at_05,
            "implied_price_kwh_for_55k": float(implied_kwh),
            "matches_any_model": any(
                abs(m["cost"] - claimed) / claimed < 0.05 for m in out["models"].values()
            ),
        }
    return out


def run_all_tests(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    cbeci: pd.DataFrame,
    network: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_config()
    processed = resolve_path(cfg, "processed")

    cost_cols = [
        c
        for c in (
            "cost_A_guess_sma7",
            "cost_A_production",
            "cost_B_sma7",
            "cost_C",
        )
        if c in costs.columns and costs[c].notna().any()
    ]

    summaries = []
    episodes = {}
    ratios = {}
    forwards = {}

    for cost_col in cost_cols:
        for price_col in ("low_min", "low_median"):
            s = breach_summary(ohlc, costs, cost_col, price_col=price_col)
            summaries.append(s)
            logger.info(
                "%s vs %s: breach_days=%d claim_holds=%s first=%s",
                cost_col,
                price_col,
                s["breach_days_low"],
                s["claim_never_holds"],
                s["first_breach_date"],
            )

        ep = episode_table(ohlc, costs, cost_col, price_col="low_median", cfg=cfg)
        episodes[cost_col] = ep
        save_parquet(ep, processed / f"episodes_{cost_col}.parquet")

        ratios[cost_col] = {k: v for k, v in ratio_analysis(ohlc, costs, cost_col).items() if k != "series"}
        ratios[cost_col]["_series"] = ratio_analysis(ohlc, costs, cost_col)["series"]

        forwards[cost_col] = forward_returns_signal(ohlc, costs, cost_col, cfg)

    sens_a = sensitivity_grid(ohlc, cbeci, network, cfg, model="A")
    sens_b = sensitivity_grid(ohlc, cbeci, network, cfg, model="B")
    save_parquet(sens_a, processed / "sensitivity_A.parquet")
    save_parquet(sens_b, processed / "sensitivity_B.parquet")

    summary_df = pd.DataFrame(summaries)
    save_parquet(summary_df, processed / "breach_summary.parquet")

    state = current_state(ohlc, costs, cbeci, network, cfg)

    # Primary claim = electrical cost (Model A guess / Model B), not Capriole "production"
    # cost (electrical / 0.6), which is a wider and weaker floor.
    elec_mask = summary_df["cost_col"].isin(["cost_A_guess_sma7", "cost_B_sma7"])
    elec = summary_df[elec_mask & (summary_df["price_col"] == "low_median")]
    prod = summary_df[
        (summary_df["cost_col"] == "cost_A_production")
        & (summary_df["price_col"] == "low_median")
    ]
    elec_breaches = int(elec["breach_days_low"].sum()) if len(elec) else 0
    any_elec = elec_breaches > 0
    first_elec = elec["first_breach_date"].dropna().min() if len(elec) else None
    prod_days = int(prod["breach_days_low"].iloc[0]) if len(prod) else 0

    if any_elec:
        evidence = (
            f"NO — the literal claim is false. Using consensus low_median vs Model A "
            f"electrical cost, price traded below the line on "
            f"{int(elec.loc[elec['cost_col']=='cost_A_guess_sma7','breach_days_low'].sum())} "
            f"days (first {first_elec}). Model B bottom-up: "
            f"{int(elec.loc[elec['cost_col']=='cost_B_sma7','breach_days_low'].sum())} days. "
            f"Capriole-style production cost (electrical/0.6) was breached on {prod_days} days. "
            f"CBECI's 2023 downward history revision reduces how often Model A looks breached "
            f"ex-post — see caveats."
        )
    else:
        evidence = (
            f"YES for electrical-cost models in this revised-CBECI sample "
            f"(0 low_median breach days on Model A/B), but Capriole production cost "
            f"(÷0.6) was breached on {prod_days} days — so a 'production cost floor' "
            f"claim would still be false. Results are highly sensitive to $/kWh and "
            f"look-ahead bias from CBECI revisions."
        )

    verdict = {
        "claim": "Bitcoin NEVER drops below its electrical cost of production",
        "holds": not any_elec,
        "evidence": evidence,
    }

    return {
        "verdict": verdict,
        "summaries": summary_df,
        "episodes": episodes,
        "sensitivity_A": sens_a,
        "sensitivity_B": sens_b,
        "forward_returns": forwards,
        "ratios": ratios,
        "current_state": state,
    }


if __name__ == "__main__":
    setup_logging()
    from src.fetch_cbeci import fetch_cbeci
    from src.fetch_coinmetrics import fetch_coinmetrics
    from src.fetch_ohlc import fetch_all_ohlc
    from src.models import build_all_costs

    cfg = load_config()
    ohlc = fetch_all_ohlc(cfg)
    net = fetch_coinmetrics(cfg)
    cb = fetch_cbeci(cfg)
    costs = build_all_costs(cb, net, cfg)
    results = run_all_tests(ohlc, costs, cb, net, cfg)
    print(results["verdict"])
