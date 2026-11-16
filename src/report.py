"""Build report.html — Plotly charts + tables + one-paragraph verdict."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from plotly.offline import plot as plotly_plot

from src.config_loader import load_config, resolve_path
from src.fetch_utils import setup_logging

logger = logging.getLogger(__name__)


def _fig_main_chart(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    episodes: pd.DataFrame | None,
) -> go.Figure:
    df = ohlc.copy()
    df["date"] = pd.to_datetime(df["date"])
    c = costs.copy()
    c["date"] = pd.to_datetime(c["date"])
    m = df.merge(c, on="date", how="left")

    fig = go.Figure()

    # Candlesticks on log scale (consensus)
    fig.add_trace(
        go.Candlestick(
            x=m["date"],
            open=m["open"],
            high=m["high"],
            low=m["low_median"],
            close=m["close"],
            name="BTC-USD (consensus)",
            increasing_line_color="#2ca02c",
            decreasing_line_color="#d62728",
        )
    )

    cost_traces = [
        ("cost_A_guess_sma7", "Model A electrical (CBECI guess, 7d)", "#1f77b4"),
        ("cost_A_min", "Model A min", "#aec7e8"),
        ("cost_A_max", "Model A max", "#aec7e8"),
        ("cost_A_production", "Model A production (/0.6)", "#ff7f0e"),
        ("cost_B_sma7", "Model B bottom-up (7d)", "#9467bd"),
        ("cost_C", "Model C Capriole", "#8c564b"),
    ]
    for col, name, color in cost_traces:
        if col not in m.columns or m[col].isna().all():
            continue
        width = 1 if "min" in col or "max" in col else 2
        dash = "dot" if "min" in col or "max" in col else "solid"
        fig.add_trace(
            go.Scatter(
                x=m["date"],
                y=m[col],
                name=name,
                mode="lines",
                line=dict(color=color, width=width, dash=dash),
            )
        )

    # Shade breach episodes (Model A guess vs low_median)
    if episodes is not None and not episodes.empty:
        for _, ep in episodes.iterrows():
            fig.add_vrect(
                x0=ep["start"],
                x1=ep["end"],
                fillcolor="rgba(214, 39, 40, 0.15)",
                layer="below",
                line_width=0,
            )

    fig.update_layout(
        title="Bitcoin price vs electrical / production cost",
        yaxis_type="log",
        yaxis_title="USD (log)",
        xaxis_title="Date",
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        height=640,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def _fig_ratio(ratio_series: pd.DataFrame, cost_col: str, bands: dict[str, float]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=ratio_series["date"],
            y=ratio_series["ratio"],
            name="price / cost",
            line=dict(color="#1f77b4", width=1.5),
        )
    )
    for key, val in bands.items():
        fig.add_hline(y=val, line_dash="dot", annotation_text=key, annotation_position="right")
    fig.add_hline(y=1.0, line_color="red", line_width=1.5, annotation_text="cost floor")
    fig.update_layout(
        title=f"Price / {cost_col}",
        yaxis_title="Ratio",
        template="plotly_white",
        height=360,
    )
    return fig


def _fig_buy_zone(bands: pd.DataFrame, horizon: int = 365) -> go.Figure:
    """Bar chart of median forward return by %‑above‑cost band (non-overlapping)."""
    sub = bands[(bands["kind"] == "band") & (bands["horizon"] == horizon)].copy()
    if sub.empty:
        return go.Figure()
    colors = ["#2ca02c" if x > 0 else "#d62728" for x in sub["excess_median_pp"].fillna(0)]
    fig = go.Figure(
        data=[
            go.Bar(
                x=sub["band"],
                y=sub["median_ret_pct"],
                marker_color=colors,
                text=[
                    f"n={int(n)}<br>ex {ex:+.0f}pp"
                    for n, ex in zip(sub["n_episodes"], sub["excess_median_pp"])
                ],
                textposition="outside",
                name="Median forward return",
            )
        ]
    )
    base = bands[(bands["kind"] == "baseline") & (bands["horizon"] == horizon)]
    if not base.empty:
        fig.add_hline(
            y=float(base.iloc[0]["median_ret_pct"]),
            line_dash="dot",
            line_color="#555",
            annotation_text="baseline median",
        )
    fig.update_layout(
        title=f"Buy-zone bands — median {horizon}d return (non-overlapping entries)",
        xaxis_title="% above electrical cost",
        yaxis_title="Median forward return (%)",
        template="plotly_white",
        height=420,
    )
    return fig


def _fig_sensitivity(sens: pd.DataFrame, title: str) -> go.Figure:
    if sens.empty:
        return go.Figure()
    if sens["efficiency_multiplier"].nunique() == 1:
        # Model A: bar by $/kWh
        fig = go.Figure(
            go.Bar(
                x=sens["price_kwh"].astype(str),
                y=sens["breach_days"],
                text=sens["breach_pct"].round(2).astype(str) + "%",
                textposition="auto",
                marker_color="#d62728",
            )
        )
        fig.update_layout(title=title, xaxis_title="$/kWh", yaxis_title="Breach days", template="plotly_white", height=360)
        return fig

    pivot = sens.pivot(index="efficiency_multiplier", columns="price_kwh", values="breach_days")
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=[str(c) for c in pivot.columns],
            y=[str(i) for i in pivot.index],
            colorscale="YlOrRd",
            text=pivot.values,
            texttemplate="%{text}",
            colorbar_title="Breach days",
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="$/kWh",
        yaxis_title="Efficiency multiplier",
        template="plotly_white",
        height=400,
    )
    return fig


def _df_html(df: pd.DataFrame, float_fmt: str = "%.2f") -> str:
    if df is None or df.empty:
        return "<p><em>No data</em></p>"
    return df.to_html(index=False, float_format=lambda x: float_fmt % x if pd.notna(x) else "", border=0, classes="tbl")


def build_report(
    ohlc: pd.DataFrame,
    costs: pd.DataFrame,
    results: dict[str, Any],
    cfg: dict[str, Any] | None = None,
) -> str:
    cfg = cfg or load_config()
    out_path = resolve_path(cfg, "report")

    verdict = results["verdict"]
    holds = verdict["holds"]
    verdict_color = "#2ca02c" if holds else "#d62728"
    verdict_word = "YES" if holds else "NO"

    ep_a = results["episodes"].get("cost_A_guess_sma7", pd.DataFrame())
    fig_main = _fig_main_chart(ohlc, costs, ep_a if isinstance(ep_a, pd.DataFrame) else None)

    ratio_key = "cost_A_guess_sma7"
    ratio_full = results["ratios"].get(ratio_key, {})
    ratio_series = ratio_full.get("_series")
    bands = {
        "p5": ratio_full.get("ratio_p5", 0),
        "p50": ratio_full.get("ratio_p50", 0),
        "p95": ratio_full.get("ratio_p95", 0),
    }
    fig_ratio = _fig_ratio(ratio_series, ratio_key, bands) if ratio_series is not None else go.Figure()

    fig_sens_a = _fig_sensitivity(results["sensitivity_A"], "Sensitivity — Model A breach days by $/kWh")
    fig_sens_b = _fig_sensitivity(results["sensitivity_B"], "Sensitivity — Model B breach days ($/kWh × efficiency)")

    main_div = plotly_plot(fig_main, output_type="div", include_plotlyjs="cdn")
    ratio_div = plotly_plot(fig_ratio, output_type="div", include_plotlyjs=False)
    sens_a_div = plotly_plot(fig_sens_a, output_type="div", include_plotlyjs=False)
    sens_b_div = plotly_plot(fig_sens_b, output_type="div", include_plotlyjs=False)

    summary_df = results["summaries"]
    # Prefer readable columns
    show_cols = [
        c
        for c in (
            "cost_col",
            "price_col",
            "n_days",
            "breach_days_low",
            "breach_pct_low",
            "breach_days_close",
            "breach_weeks_close",
            "first_breach_date",
            "claim_never_holds",
        )
        if c in summary_df.columns
    ]

    state = results["current_state"]
    state_rows = []
    for label, m in state.get("models", {}).items():
        state_rows.append(
            {
                "model": label,
                "as_of": m["date"],
                "cost_usd": m["cost"],
                "price_pct_above_cost": m["pct_above_cost"],
            }
        )
    state_df = pd.DataFrame(state_rows)
    claimed = state.get("claimed_55k", {})

    # Forward returns — Model A
    fwd = results["forward_returns"].get("cost_A_guess_sma7", pd.DataFrame())

    # Episodes table (top by max % below)
    ep_html = _df_html(ep_a.head(25) if isinstance(ep_a, pd.DataFrame) else pd.DataFrame())

    buy_zone = results.get("buy_zone") or {}
    bz_rec = buy_zone.get("recommendation", {})
    bz_bands = buy_zone.get("bands", pd.DataFrame())
    bz_prox = buy_zone.get("proximity", pd.DataFrame())
    fig_bz = _fig_buy_zone(bz_bands, horizon=365) if isinstance(bz_bands, pd.DataFrame) else go.Figure()
    bz_div = plotly_plot(fig_bz, output_type="div", include_plotlyjs=False)

    bz_365 = (
        bz_bands[(bz_bands["kind"].isin(["band", "baseline"])) & (bz_bands["horizon"] == 365)][
            [
                "band",
                "n_episodes",
                "median_ret_pct",
                "mean_ret_pct",
                "hit_rate_pct",
                "mean_mdd_pct",
                "excess_median_pp",
            ]
        ]
        if isinstance(bz_bands, pd.DataFrame) and not bz_bands.empty
        else pd.DataFrame()
    )
    prox_365 = (
        bz_prox[bz_prox["horizon"] == 365][
            [
                "within_pct",
                "n_episodes",
                "median_ret_pct",
                "mean_ret_pct",
                "hit_rate_pct",
                "mean_mdd_pct",
                "excess_median_pp",
            ]
        ]
        if isinstance(bz_prox, pd.DataFrame) and not bz_prox.empty
        else pd.DataFrame()
    )
    rec_lo, rec_hi = bz_rec.get("best_edge_range_pct", [0, 20])
    stretch_hi = bz_rec.get("stretch_range_pct", [0, 30])[1] if bz_rec.get("stretch_range_pct") else 30
    fade = bz_rec.get("fade_above_pct", 40)
    cur_pct = bz_rec.get("current_pct_above", float("nan"))
    in_rec = bz_rec.get("in_recommended", False)
    in_stretch = bz_rec.get("in_stretch", False)
    zone_status = (
        "inside recommended 0–20% zone"
        if in_rec
        else ("inside stretch zone (≤30%)" if in_stretch else "outside accumulation zone")
    )

    caveats = """
    <h2>Methodological caveats</h2>
    <ul>
      <li><strong>Reflexivity / circularity:</strong> Hashrate follows price, so the cost line
      rises with bull markets and falls after crashes (miner capitulation lowers difficulty).
      The &ldquo;floor&rdquo; partly moves to meet price and is not an independent support level.</li>
      <li><strong>Unknown inputs:</strong> Global $/kWh and fleet efficiency are assumptions.
      Small changes move the line by thousands of dollars (JPMorgan estimated ~$4,300 per 1&cent;/kWh in 2023).
      See the sensitivity heatmaps. At $0.03/kWh Model A never breaches; at $0.08/kWh it is below
      cost on ~25% of days — the &ldquo;floor&rdquo; is an assumption, not a law.</li>
      <li><strong>Look-ahead bias:</strong> CBECI revised methodology in 2023 and revised history
      downward. Under today&rsquo;s revised series, Nov 2022 (FTX) does <em>not</em> breach Model A
      electrical cost (~$14K) even though price sat near $16K; it <em>does</em> breach Capriole-style
      production cost (electrical/0.6). Capriole charts can also lag by weeks. The line you see
      today may not be what was visible in real time.</li>
      <li><strong>Venue differences:</strong> Single-exchange wicks (e.g. COVID crash prints) can
      create or erase breaches. Every test is run on both <code>low_min</code> and
      <code>low_median</code>.</li>
      <li><strong>Survivorship and small n:</strong> Independent Model A electrical breach episodes
      number only about four in this sample. Forward-return stats are illustrative, not a trading
      system backtest with meaningful statistical power.</li>
    </ul>
    """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Bitcoin Electrical Cost — Falsification Report</title>
  <style>
    body {{ font-family: "IBM Plex Sans", "Segoe UI", sans-serif; margin: 2rem auto; max-width: 1100px;
           color: #1a1a1a; line-height: 1.45; background: #fafafa; padding: 0 1rem 3rem; }}
    h1, h2 {{ font-family: "IBM Plex Serif", Georgia, serif; }}
    .verdict {{ border-left: 6px solid {verdict_color}; background: #fff; padding: 1rem 1.25rem;
                margin: 1.5rem 0; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    .verdict .ans {{ font-size: 1.6rem; font-weight: 700; color: {verdict_color}; }}
    table.tbl {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; background: #fff; }}
    table.tbl th, table.tbl td {{ border-bottom: 1px solid #ddd; padding: 0.4rem 0.55rem; text-align: left; }}
    table.tbl th {{ background: #f0f0f0; }}
    .meta {{ color: #555; font-size: 0.9rem; }}
    code {{ background: #eee; padding: 0.1rem 0.3rem; }}
  </style>
</head>
<body>
  <h1>Bitcoin Electrical Cost — Falsification Backtest</h1>
  <p class="meta">Claim tested: &ldquo;Bitcoin NEVER drops below its electrical cost of production.&rdquo;
  Price = consensus daily OHLC (Bitstamp / Coinbase / Kraken). Cost models A (CBECI), B (bottom-up), C (Capriole if provided).</p>

  <div class="verdict">
    <div class="ans">Does the claim hold? {verdict_word}</div>
    <p>{verdict["evidence"]}</p>
  </div>

  <h2>Main chart</h2>
  {main_div}

  <h2>Breach summary</h2>
  {_df_html(summary_df[show_cols])}

  <h2>Breach episodes (Model A guess, low_median)</h2>
  <p class="meta">Consecutive days with daily low &lt; cost. Forward returns from episode start close.</p>
  {ep_html}

  <h2>Sensitivity</h2>
  {sens_a_div}
  {sens_b_div}
  <p class="meta">Question answered: at what assumption does the &ldquo;floor&rdquo; stop being a floor?
  When breach_days &gt; 0 the literal claim fails for that parameter set. Higher $/kWh or worse
  efficiency (multiplier &gt; 1 on Model B) raises the cost line and increases breaches.</p>

  <h2>Forward returns (tradeable version)</h2>
  <p class="meta">Signal = close below cost or within X% of cost. Compared to unconditional baseline.
  <strong>Warning:</strong> sample size is small (~5–6 independent episodes); treat as descriptive.</p>
  {_df_html(fwd)}

  <h2>Buy-zone study — best % above electrical cost</h2>
  <div class="verdict">
    <div class="ans">Recommended buy range: {rec_lo:.0f}–{rec_hi:.0f}% above cost</div>
    <p>Stretch zone up to ~{stretch_hi:.0f}%. Edge fades above ~{fade:.0f}%.
    Today: <strong>{cur_pct:.1f}%</strong> above Model A electrical cost — {zone_status}.</p>
    <p class="meta">{bz_rec.get("narrative", "")}</p>
  </div>
  {bz_div}
  <p class="meta">Method: one entry at the start of each contiguous stay in a band (non-overlapping),
  then measure 365-day forward returns vs a calendar baseline (buy every 365 days).
  Green bars = positive excess median vs baseline.</p>
  <h3>Bands (365d)</h3>
  {_df_html(bz_365)}
  <h3>Proximity rule — enter when first within X% of cost (365d)</h3>
  {_df_html(prox_365)}

  <h2>Price / cost ratio</h2>
  {ratio_div}
  <p>Current ratio = <strong>{ratio_full.get("current_ratio", float("nan")):.3f}</strong>
  (percentile {ratio_full.get("percentile", float("nan")):.1f}% of history).
  Price ${ratio_full.get("current_price", float("nan")):,.0f} vs cost
  ${ratio_full.get("current_cost", float("nan")):,.0f} on {ratio_full.get("current_date", "?")}.</p>

  <h2>Current state</h2>
  <p>Spot (consensus close): <strong>${state["price"]:,.0f}</strong> as of {state["price_date"]}.</p>
  {_df_html(state_df)}
  <p>Claimed current electrical cost of <strong>$55,000</strong>:
  Model A guess at $0.05/kWh = ${claimed.get("model_A_guess_at_0.05", float("nan")):,.0f}.
  Implied $/kWh to hit $55K = {claimed.get("implied_price_kwh_for_55k", float("nan")):.3f}.
  Matches any model within 5%: {claimed.get("matches_any_model", False)}.</p>

  {caveats}

  <p class="meta">Generated by btc-electrical-cost. CBECI data &copy; Cambridge Centre for Alternative Finance
  (CC BY-NC-SA 4.0). Re-run: <code>python -m src.pipeline</code></p>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    logger.info("Wrote report to %s", out_path)
    return str(out_path)


if __name__ == "__main__":
    setup_logging()
    print("Use: python -m src.pipeline")
