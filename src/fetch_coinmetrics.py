"""Fetch BTC network metrics from Coin Metrics Community API (free tier).

Falls back to blockchain.info charts if a metric is not free.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.config_loader import load_config, resolve_path
from src.fetch_utils import (
    assert_max_gap,
    http_get,
    load_parquet,
    merge_incremental,
    save_parquet,
    save_raw_json,
    setup_logging,
    sleep_polite,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

CATALOG_URL = "https://community-api.coinmetrics.io/v4/catalog-v2/asset-metrics"
TIMESERIES_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

# DiffMean is often paid-only on Community; requesting it 400s the whole call.
DESIRED_METRICS = [
    "PriceUSD",
    "HashRate",
    "IssTotNtv",
    "FeeTotNtv",
    "BlkCnt",
]
OPTIONAL_METRICS = [
    "DiffMean",
]

BLOCKCHAIN_INFO = {
    "HashRate": "https://api.blockchain.info/charts/hash-rate?timespan=all&format=json",
    "DiffMean": "https://api.blockchain.info/charts/difficulty?timespan=all&format=json",
    "FeeTotNtv": "https://api.blockchain.info/charts/transaction-fees?timespan=all&format=json",
}


def confirm_free_metrics(cfg: dict[str, Any] | None = None) -> list[str]:
    """Query catalog and return metrics that are available on the community tier."""
    cfg = cfg or load_config()
    timeout = cfg["fetch"]["request_timeout_s"]
    raw_dir = resolve_path(cfg, "raw")

    resp = http_get(CATALOG_URL, params={"assets": "btc"}, timeout=timeout)
    payload = resp.json()
    save_raw_json(raw_dir / "coinmetrics_catalog.json", payload, {"url": CATALOG_URL})

    available: set[str] = set()
    for asset in payload.get("data", []):
        for m in asset.get("metrics", []):
            # Community API only returns free metrics in this endpoint
            metric_id = m.get("metric") or m.get("full_name") or m.get("id")
            if metric_id:
                available.add(metric_id)
            # Also collect from frequencies if nested differently
            for freq in m.get("frequencies", []) or []:
                mid = freq.get("metric") or m.get("metric")
                if mid:
                    available.add(mid)
        # Alternate schema: metrics listed as strings
        if isinstance(asset.get("metrics"), list) and asset["metrics"] and isinstance(asset["metrics"][0], str):
            available.update(asset["metrics"])

    # Flatten nested catalog variants
    if not available:
        # Try scraping metric names from raw text keys
        text = str(payload)
        for m in DESIRED_METRICS:
            if m in text:
                available.add(m)

    free = [m for m in DESIRED_METRICS if m in available or not available]
    # If catalog parse failed, still attempt the known-free desired set
    if not available:
        logger.warning("Catalog parse returned no metric ids; requesting %s", DESIRED_METRICS)
        return list(DESIRED_METRICS)
    missing = [m for m in DESIRED_METRICS if m not in available]
    logger.info("Coin Metrics free metrics confirmed: %s", [m for m in DESIRED_METRICS if m in available])
    if missing:
        logger.warning("Not confirmed free (will fall back if fetch fails): %s", missing)
        free = [m for m in DESIRED_METRICS if m in available]
    return free or list(DESIRED_METRICS)


def _fetch_cm_pages(metrics: list[str], timeout: int) -> list[dict]:
    params = {
        "assets": "btc",
        "metrics": ",".join(metrics),
        "frequency": "1d",
        "page_size": 10000,
    }
    rows: list[dict] = []
    url: str | None = TIMESERIES_URL
    first = True
    while url:
        if first:
            resp = http_get(url, params=params, timeout=timeout)
            first = False
        else:
            resp = http_get(url, timeout=timeout)
        body = resp.json()
        rows.extend(body.get("data", []))
        url = body.get("next_page_url")
        sleep_polite(0.25)
    return rows


def fetch_blockchain_info_chart(url: str, col_name: str, timeout: int) -> pd.DataFrame:
    resp = http_get(url, timeout=timeout)
    payload = resp.json()
    values = payload.get("values", [])
    df = pd.DataFrame(values)
    df["date"] = pd.to_datetime(df["x"], unit="s", utc=True).dt.tz_localize(None).dt.normalize()
    df[col_name] = pd.to_numeric(df["y"], errors="coerce")
    return df[["date", col_name]]


def subsidy_on_date(date: pd.Timestamp, schedule: list[dict]) -> float:
    """Return block subsidy for a calendar date from the halving schedule."""
    sub = schedule[0]["subsidy"]
    for row in schedule:
        if pd.Timestamp(date) >= pd.Timestamp(row["date"]):
            sub = float(row["subsidy"])
    return sub


def fetch_coinmetrics(cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    raw_dir = resolve_path(cfg, "raw")
    out_path = raw_dir / "coinmetrics_btc.parquet"
    timeout = cfg["fetch"]["request_timeout_s"]
    max_gap = cfg["fetch"]["max_gap_days"]

    existing = load_parquet(out_path) if out_path.exists() else None
    metrics = confirm_free_metrics(cfg)

    rows: list[dict] = []
    try:
        rows = _fetch_cm_pages(metrics, timeout)
    except Exception as exc:
        logger.warning("Coin Metrics fetch failed for %s: %s — retrying core set", metrics, exc)
        core = ["HashRate", "IssTotNtv", "FeeTotNtv", "BlkCnt", "PriceUSD"]
        try:
            rows = _fetch_cm_pages(core, timeout)
        except Exception as exc2:
            logger.warning("Core Coin Metrics fetch failed: %s", exc2)
            rows = []

    if rows:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["time"]).dt.tz_localize(None).dt.normalize()
        for m in DESIRED_METRICS:
            if m in df.columns:
                df[m] = pd.to_numeric(df[m], errors="coerce")
        keep = ["date"] + [m for m in DESIRED_METRICS if m in df.columns]
        df = df[keep].drop_duplicates("date")
    else:
        df = pd.DataFrame(columns=["date"])

    # Fallbacks for missing series
    for metric, url in BLOCKCHAIN_INFO.items():
        if metric not in df.columns or df[metric].isna().all():
            logger.info("Falling back to blockchain.info for %s", metric)
            fb = fetch_blockchain_info_chart(url, metric, timeout)
            if df.empty or "date" not in df.columns:
                df = fb
            else:
                df = df.merge(fb, on="date", how="outer", suffixes=("", "_fb"))
                if f"{metric}_fb" in df.columns:
                    df[metric] = df[metric].fillna(df[f"{metric}_fb"]) if metric in df.columns else df[f"{metric}_fb"]
                    df = df.drop(columns=[f"{metric}_fb"])

    # Miners-revenue fallback (USD) — convert later if needed
    if "FeeTotNtv" not in df.columns or df["FeeTotNtv"].isna().all():
        logger.info("Attempting blockchain.info transaction-fees for FeeTotNtv")
        try:
            fb = fetch_blockchain_info_chart(BLOCKCHAIN_INFO["FeeTotNtv"], "FeeTotNtv", timeout)
            df = df.merge(fb, on="date", how="outer") if not df.empty else fb
        except Exception as exc:
            logger.warning("Fee fallback failed: %s", exc)

    df = df.sort_values("date").reset_index(drop=True)
    df["fetched_at"] = utc_now_iso()

    # Build daily reward from subsidy schedule + fees when IssTotNtv missing
    schedule = cfg["halving"]["schedule"]
    if "BlkCnt" not in df.columns or df["BlkCnt"].isna().all():
        # ~144 blocks/day expected; leave NaN and derive reward from IssTotNtv alone
        df["BlkCnt"] = np.nan

    df["subsidy_per_block"] = df["date"].apply(lambda d: subsidy_on_date(d, schedule))
    df["reward_subsidy_est"] = df["subsidy_per_block"] * df["BlkCnt"].fillna(144.0)

    if "IssTotNtv" in df.columns:
        # IssTotNtv is daily issuance in BTC (subsidy total)
        df["reward_issuance"] = df["IssTotNtv"]
        # Cross-check: median absolute % error where both exist
        both = df.dropna(subset=["IssTotNtv", "reward_subsidy_est"])
        if len(both) > 100:
            mape = ((both["IssTotNtv"] - both["reward_subsidy_est"]).abs() / both["IssTotNtv"].clip(lower=1e-9)).median()
            logger.info("IssTotNtv vs subsidy×BlkCnt median abs %% error: %.2f%%", 100 * mape)
    else:
        df["reward_issuance"] = df["reward_subsidy_est"]

    fees = df["FeeTotNtv"] if "FeeTotNtv" in df.columns else 0.0
    df["reward_btc"] = df["reward_issuance"].fillna(df["reward_subsidy_est"]) + fees.fillna(0.0)

    ma = cfg["smoothing"]["reward_ma_days"]
    df["reward_btc_7dma"] = df["reward_btc"].rolling(ma, min_periods=max(1, ma // 2)).mean()

    # Coin Metrics community HashRate for BTC is in TH/s (recent ~1e8–1e9 = hundreds of EH/s).
    # Only convert when values are clearly H/s (>> 1e15) or EH/s (median << 1e5 in modern era).
    if "HashRate" in df.columns:
        df["hashrate_th_s"] = pd.to_numeric(df["HashRate"], errors="coerce")
        recent = df.loc[df["date"] >= "2023-01-01", "hashrate_th_s"]
        med_recent = recent.median() if len(recent) else df["hashrate_th_s"].median()
        if pd.notna(med_recent) and med_recent > 1e15:
            logger.info("HashRate recent median %.2e looks like H/s; converting to TH/s (/1e12)", med_recent)
            df["hashrate_th_s"] = df["hashrate_th_s"] / 1e12
        elif pd.notna(med_recent) and med_recent < 1e5:
            # Likely already EH/s
            logger.info("HashRate recent median %.2e looks like EH/s; converting to TH/s (*1e6)", med_recent)
            df["hashrate_th_s"] = df["hashrate_th_s"] * 1e6
        else:
            logger.info("HashRate treated as TH/s (recent median %.2e)", med_recent)

    merged = merge_incremental(existing, df, "date")
    # Gap check only on post-2013 dense period
    dense = merged[merged["date"] >= "2013-01-01"].copy()
    if not dense.empty:
        assert_max_gap(dense, "date", max_gap, "coinmetrics")
    save_parquet(merged, out_path)
    return merged


if __name__ == "__main__":
    setup_logging()
    out = fetch_coinmetrics()
    print(out.tail())
    print(out.columns.tolist())
