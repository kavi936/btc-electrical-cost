"""Fetch daily BTC-USD OHLC from Bitstamp, Coinbase Exchange, and Kraken.

Builds a consensus bar: median OHLCV + low_min / low_median.
Downloads are idempotent — only missing dates are requested.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
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
    setup_logging,
    sleep_polite,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

BITSTAMP_URL = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
COINBASE_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"


# ---------------------------------------------------------------------------
# Bitstamp
# ---------------------------------------------------------------------------

def fetch_bitstamp_chunk(start_unix: int, limit: int = 1000, timeout: int = 60) -> list[dict]:
    params = {"step": 86400, "limit": limit, "start": start_unix}
    resp = http_get(BITSTAMP_URL, params=params, timeout=timeout)
    data = resp.json().get("data", {}).get("ohlc", [])
    return data


def fetch_bitstamp(cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    raw_dir = resolve_path(cfg, "raw")
    out_path = raw_dir / "ohlc_bitstamp.parquet"
    timeout = cfg["fetch"]["request_timeout_s"]
    max_gap = cfg["fetch"]["max_gap_days"]

    existing = load_parquet(out_path) if out_path.exists() else None
    if existing is not None and not existing.empty:
        last = pd.to_datetime(existing["date"]).max()
        start_unix = int(last.timestamp()) + 86400
        logger.info("Bitstamp resume from %s", last.date())
    else:
        start_unix = int(cfg["fetch"]["bitstamp_start_unix"])
        logger.info("Bitstamp full history from unix %s", start_unix)

    now_unix = int(datetime.now(timezone.utc).timestamp())
    rows: list[dict] = []
    cursor = start_unix
    # Bitstamp can return <1000 bars in a window (missing early days); do NOT
    # stop on short chunks — only stop when we catch up to "now" or stall.
    while cursor < now_unix - 86400:
        chunk = fetch_bitstamp_chunk(cursor, timeout=timeout)
        sleep_polite()
        if not chunk:
            break
        rows.extend(chunk)
        last_ts = int(chunk[-1]["timestamp"])
        next_cursor = last_ts + 86400
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        # Caught up to present (last bar within 2 days of now)
        if last_ts >= now_unix - 2 * 86400:
            break

    if not rows and existing is not None:
        assert_max_gap(existing, "date", max_gap, "bitstamp")
        return existing

    new = pd.DataFrame(rows)
    if new.empty and existing is None:
        raise RuntimeError("Bitstamp returned no OHLC data")

    if not new.empty:
        new["date"] = pd.to_datetime(new["timestamp"].astype(int), unit="s", utc=True).dt.tz_localize(None).dt.normalize()
        for col in ("open", "high", "low", "close", "volume"):
            new[col] = pd.to_numeric(new[col], errors="coerce")
        new = new[["date", "open", "high", "low", "close", "volume"]].drop_duplicates("date")
        new["venue"] = "bitstamp"
        new["fetched_at"] = utc_now_iso()

    merged = merge_incremental(existing, new if not new.empty else pd.DataFrame(), "date")
    assert_max_gap(merged, "date", max_gap, "bitstamp")
    save_parquet(merged, out_path)
    return merged


# ---------------------------------------------------------------------------
# Coinbase Exchange
# ---------------------------------------------------------------------------

def fetch_coinbase_chunk(start: datetime, end: datetime, timeout: int = 60) -> list[list]:
    params = {
        "granularity": 86400,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    headers = {"User-Agent": "btc-electrical-cost-research/1.0"}
    resp = http_get(COINBASE_URL, params=params, headers=headers, timeout=timeout)
    return resp.json()


def fetch_coinbase(cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    raw_dir = resolve_path(cfg, "raw")
    out_path = raw_dir / "ohlc_coinbase.parquet"
    timeout = cfg["fetch"]["request_timeout_s"]
    max_gap = cfg["fetch"]["max_gap_days"]

    existing = load_parquet(out_path) if out_path.exists() else None
    if existing is not None and not existing.empty:
        start = pd.to_datetime(existing["date"]).max() + pd.Timedelta(days=1)
        logger.info("Coinbase resume from %s", start.date())
    else:
        start = pd.Timestamp(cfg["fetch"]["coinbase_start"])
        logger.info("Coinbase full history from %s", start.date())

    end = pd.Timestamp.utcnow().normalize().tz_localize(None)
    if start.tzinfo is not None:
        start = start.tz_localize(None)
    if pd.Timestamp(start) >= end:
        logger.info("Coinbase already up to date")
        assert_max_gap(existing, "date", max_gap, "coinbase")
        return existing

    rows: list[list] = []
    # Coinbase returns at most 300 candles per request
    cursor = pd.Timestamp(start).to_pydatetime()
    end_dt = end.to_pydatetime()
    while cursor < end_dt:
        chunk_end = min(cursor + pd.Timedelta(days=299).to_pytimedelta(), end_dt)
        chunk = fetch_coinbase_chunk(cursor, chunk_end, timeout=timeout)
        sleep_polite(0.4)
        if chunk:
            rows.extend(chunk)
        cursor = chunk_end + pd.Timedelta(days=1).to_pytimedelta()

    if not rows and existing is not None:
        assert_max_gap(existing, "date", max_gap, "coinbase")
        return existing

    # Coinbase candle: [time, low, high, open, close, volume]
    new = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    if new.empty and existing is None:
        raise RuntimeError("Coinbase returned no OHLC data")

    if not new.empty:
        new["date"] = pd.to_datetime(new["time"], unit="s", utc=True).dt.tz_localize(None).dt.normalize()
        for col in ("open", "high", "low", "close", "volume"):
            new[col] = pd.to_numeric(new[col], errors="coerce")
        new = new[["date", "open", "high", "low", "close", "volume"]].drop_duplicates("date")
        new["venue"] = "coinbase"
        new["fetched_at"] = utc_now_iso()

    merged = merge_incremental(existing, new if not new.empty else pd.DataFrame(), "date")
    assert_max_gap(merged, "date", max_gap, "coinbase")
    save_parquet(merged, out_path)
    return merged


# ---------------------------------------------------------------------------
# Kraken
# ---------------------------------------------------------------------------

def fetch_kraken_since(since: int, timeout: int = 60) -> tuple[list, int | None]:
    params = {"pair": "XBTUSD", "interval": 1440, "since": since}
    resp = http_get(KRAKEN_URL, params=params, timeout=timeout)
    body = resp.json()
    if body.get("error"):
        raise RuntimeError(f"Kraken error: {body['error']}")
    result = body["result"]
    # Key is pair name, e.g. XXBTZUSD
    pair_key = [k for k in result if k != "last"][0]
    candles = result[pair_key]
    last = result.get("last")
    return candles, last


def fetch_kraken(cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    """Kraken public OHLC only returns the most recent ~720 daily bars.

    Used as a recent-period cross-check, not as the long-history backbone.
    """
    cfg = cfg or load_config()
    raw_dir = resolve_path(cfg, "raw")
    out_path = raw_dir / "ohlc_kraken.parquet"
    timeout = cfg["fetch"]["request_timeout_s"]
    max_gap = cfg["fetch"]["max_gap_days"]

    existing = load_parquet(out_path) if out_path.exists() else None
    # Always request from far past; API still returns only ~720 most recent.
    since = int(pd.Timestamp(cfg["fetch"]["kraken_start"]).timestamp())
    logger.info("Kraken OHLC (API capped ~720 daily bars) since unix %s", since)

    rows: list = []
    cursor = since
    seen_last: set[int] = set()
    for _ in range(20):  # safety bound
        candles, last = fetch_kraken_since(cursor, timeout=timeout)
        sleep_polite(0.5)
        if not candles:
            break
        rows.extend(candles)
        if last is None or last in seen_last:
            break
        seen_last.add(int(last))
        if int(last) <= cursor:
            break
        cursor = int(last)

    if not rows and existing is not None:
        assert_max_gap(existing, "date", max_gap, "kraken")
        return existing

    # Kraken: [time, open, high, low, close, vwap, volume, count]
    new = pd.DataFrame(
        rows,
        columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
    )
    if new.empty and existing is None:
        raise RuntimeError("Kraken returned no OHLC data")

    if not new.empty:
        new["date"] = pd.to_datetime(new["time"].astype(int), unit="s", utc=True).dt.tz_localize(None).dt.normalize()
        for col in ("open", "high", "low", "close", "volume"):
            new[col] = pd.to_numeric(new[col], errors="coerce")
        new = new[["date", "open", "high", "low", "close", "volume"]].drop_duplicates("date")
        new["venue"] = "kraken"
        new["fetched_at"] = utc_now_iso()

    # Prefer union with cache so re-runs keep any previously collected span
    merged = merge_incremental(existing, new if not new.empty else pd.DataFrame(), "date")
    assert_max_gap(merged, "date", max_gap, "kraken")
    save_parquet(merged, out_path)
    return merged


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------

def build_consensus(
    frames: dict[str, pd.DataFrame],
    cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Median open/high/close/volume; low_min and low_median across venues."""
    cfg = cfg or load_config()
    pieces = []
    for venue, df in frames.items():
        if df is None or df.empty:
            continue
        tmp = df[["date", "open", "high", "low", "close", "volume"]].copy()
        tmp["date"] = pd.to_datetime(tmp["date"]).dt.normalize()
        tmp = tmp.add_suffix(f"_{venue}")
        tmp = tmp.rename(columns={f"date_{venue}": "date"})
        pieces.append(tmp.set_index("date"))

    if not pieces:
        raise RuntimeError("No venue OHLC available for consensus")

    wide = pieces[0]
    for p in pieces[1:]:
        wide = wide.join(p, how="outer")

    opens = [c for c in wide.columns if c.startswith("open_")]
    highs = [c for c in wide.columns if c.startswith("high_")]
    lows = [c for c in wide.columns if c.startswith("low_")]
    closes = [c for c in wide.columns if c.startswith("close_")]
    vols = [c for c in wide.columns if c.startswith("volume_")]

    out = pd.DataFrame(index=wide.index)
    out["open"] = wide[opens].median(axis=1, skipna=True)
    out["high"] = wide[highs].median(axis=1, skipna=True)
    out["close"] = wide[closes].median(axis=1, skipna=True)
    out["volume"] = wide[vols].sum(axis=1, skipna=True)
    out["low_median"] = wide[lows].median(axis=1, skipna=True)
    out["low_min"] = wide[lows].min(axis=1, skipna=True)
    # Primary low used in charts = low_median
    out["low"] = out["low_median"]
    out["n_venues"] = wide[closes].notna().sum(axis=1)
    out = out.reset_index().rename(columns={"index": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out = out.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)

    max_gap = cfg["fetch"]["max_gap_days"]
    assert_max_gap(out, "date", max_gap, "consensus_ohlc")

    processed = resolve_path(cfg, "processed")
    save_parquet(out, processed / "ohlc_consensus.parquet")
    return out


def fetch_all_ohlc(cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    frames: dict[str, pd.DataFrame] = {}
    for name, fn in (
        ("bitstamp", fetch_bitstamp),
        ("coinbase", fetch_coinbase),
        ("kraken", fetch_kraken),
    ):
        try:
            frames[name] = fn(cfg)
            logger.info("%s: %d bars", name, len(frames[name]))
        except Exception as exc:
            logger.error("%s fetch failed: %s", name, exc)
            raise
    return build_consensus(frames, cfg)


if __name__ == "__main__":
    setup_logging()
    df = fetch_all_ohlc()
    print(df.tail())
    print(f"rows={len(df)} venues_last={df['n_venues'].iloc[-1]}")
