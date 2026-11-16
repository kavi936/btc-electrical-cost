"""Shared fetch helpers: logging, caching, gap checks, HTTP with retries."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30))
def http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> requests.Response:
    logger.info("GET %s params=%s", url, params)
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp


def save_raw_json(path: Path, payload: Any, meta: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "fetched_at": utc_now_iso(),
        "meta": meta or {},
        "data": payload,
    }
    path.write_text(json.dumps(envelope, indent=2, default=str))
    logger.info("Wrote %s", path)


def load_raw_json(path: Path) -> Any:
    with path.open() as f:
        envelope = json.load(f)
    return envelope.get("data", envelope)


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    logger.info("Wrote %s (%d rows)", path, len(df))


def load_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def assert_max_gap(df: pd.DataFrame, date_col: str, max_gap_days: int, name: str) -> None:
    """Fail loudly if any consecutive gap exceeds max_gap_days."""
    if df.empty:
        raise ValueError(f"{name}: empty series")
    dates = pd.to_datetime(df[date_col]).sort_values().reset_index(drop=True)
    deltas = dates.diff().dt.days.dropna()
    bad = deltas[deltas > max_gap_days]
    if len(bad):
        idx = bad.index[0]
        raise ValueError(
            f"{name}: gap of {int(bad.iloc[0])} days between "
            f"{dates.iloc[idx - 1].date()} and {dates.iloc[idx].date()} "
            f"(max allowed {max_gap_days})"
        )


def merge_incremental(
    existing: pd.DataFrame | None,
    new: pd.DataFrame,
    date_col: str = "date",
) -> pd.DataFrame:
    """Append only rows whose date is missing from the cached frame."""
    if existing is None or existing.empty:
        return new.copy()
    existing = existing.copy()
    new = new.copy()
    existing[date_col] = pd.to_datetime(existing[date_col]).dt.normalize()
    new[date_col] = pd.to_datetime(new[date_col]).dt.normalize()
    known = set(existing[date_col])
    fresh = new[~new[date_col].isin(known)]
    if fresh.empty:
        logger.info("No new rows to append")
        return existing
    logger.info("Appending %d new rows", len(fresh))
    out = pd.concat([existing, fresh], ignore_index=True)
    return out.sort_values(date_col).reset_index(drop=True)


def sleep_polite(seconds: float = 0.35) -> None:
    time.sleep(seconds)
