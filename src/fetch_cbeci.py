"""Load Cambridge Bitcoin Electricity Consumption Index (CBECI).

Primary: download CSV from CCAF public endpoint (no official documented API,
but ccaf.io serves a download URL). Also accepts a manual drop-in at
data/manual/cbeci.csv.

NOTE: CBECI revised methodology in 2023 and revised history downward —
look-ahead bias risk when using today's revised series as if it were
real-time known.
"""

from __future__ import annotations

import logging
from io import StringIO
from typing import Any

import pandas as pd

from src.config_loader import load_config, resolve_path
from src.fetch_utils import (
    assert_max_gap,
    http_get,
    load_parquet,
    save_parquet,
    setup_logging,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

# Public download endpoint discovered on ccaf.io (versioned path may change)
CBECI_DOWNLOAD_URLS = [
    "https://ccaf.io/cbeci/api/v1.4.0/download/data",
    "https://ccaf.io/cbeci/api/v1.3.0/download/data",
]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map various CBECI CSV header spellings to canonical names."""
    rename: dict[str, str] = {}
    for c in df.columns:
        cl = str(c).strip().lower()
        if "date and time" in cl or cl == "datetime" or cl == "date":
            rename[c] = "datetime"
        elif "power max" in cl:
            rename[c] = "power_max_gw"
        elif "power min" in cl:
            rename[c] = "power_min_gw"
        elif "power guess" in cl:
            rename[c] = "power_guess_gw"
        elif "annualised consumption max" in cl or "annualized consumption max" in cl:
            rename[c] = "twh_max"
        elif "annualised consumption min" in cl or "annualized consumption min" in cl:
            rename[c] = "twh_min"
        elif "annualised consumption guess" in cl or "annualized consumption guess" in cl:
            rename[c] = "twh_guess"
        elif cl == "timestamp":
            rename[c] = "timestamp"
    return df.rename(columns=rename)


def parse_cbeci_csv(text: str) -> pd.DataFrame:
    # Skip metadata rows that start before the header containing Timestamp/Date
    lines = text.splitlines()
    header_idx = 0
    for i, line in enumerate(lines):
        if "Timestamp" in line or "Date and Time" in line or line.lower().startswith("date"):
            header_idx = i
            break
    body = "\n".join(lines[header_idx:])
    df = pd.read_csv(StringIO(body))
    df = _normalize_columns(df)

    if "datetime" in df.columns:
        df["date"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
    elif "timestamp" in df.columns:
        df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
    else:
        raise ValueError("CBECI CSV missing date/timestamp column")

    for col in ("power_max_gw", "power_min_gw", "power_guess_gw", "twh_max", "twh_min", "twh_guess"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Derive annualised TWh from GW if missing: GW * 24 * 365 / 1000 = TWh/yr
    if "twh_guess" not in df.columns and "power_guess_gw" in df.columns:
        df["twh_guess"] = df["power_guess_gw"] * 24 * 365 / 1000.0
        df["twh_min"] = df["power_min_gw"] * 24 * 365 / 1000.0
        df["twh_max"] = df["power_max_gw"] * 24 * 365 / 1000.0

    keep = ["date", "power_min_gw", "power_guess_gw", "power_max_gw", "twh_min", "twh_guess", "twh_max"]
    keep = [c for c in keep if c in df.columns]
    out = df[keep].dropna(subset=["date"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return out


def load_manual_cbeci(cfg: dict[str, Any] | None = None) -> pd.DataFrame | None:
    cfg = cfg or load_config()
    manual = resolve_path(cfg, "manual") / "cbeci.csv"
    if not manual.exists():
        return None
    logger.info("Loading manual CBECI from %s", manual)
    text = manual.read_text()
    return parse_cbeci_csv(text)


def download_cbeci(cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    timeout = cfg["fetch"]["request_timeout_s"]
    raw_dir = resolve_path(cfg, "raw")
    manual_dir = resolve_path(cfg, "manual")

    last_err: Exception | None = None
    text: str | None = None
    used_url: str | None = None
    for url in CBECI_DOWNLOAD_URLS:
        try:
            resp = http_get(url, timeout=timeout)
            text = resp.text
            used_url = url
            break
        except Exception as exc:
            logger.warning("CBECI download failed (%s): %s", url, exc)
            last_err = exc

    if text is None:
        # Fall back to manual file only
        manual = load_manual_cbeci(cfg)
        if manual is not None:
            return manual
        raise RuntimeError(f"Could not download CBECI and no manual file present: {last_err}")

    # Cache raw CSV with fetch timestamp header comment
    stamp = utc_now_iso()
    raw_path = raw_dir / "cbeci_download.csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(f"# fetched_at={stamp}\n# url={used_url}\n{text}")
    logger.info("Cached CBECI CSV to %s", raw_path)

    # Also mirror into manual/ so offline re-runs work
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / "cbeci.csv").write_text(text)

    return parse_cbeci_csv(text)


def fetch_cbeci(cfg: dict[str, Any] | None = None, *, force_download: bool = False) -> pd.DataFrame:
    """Idempotent CBECI load: reuse parquet unless force_download or missing."""
    cfg = cfg or load_config()
    raw_dir = resolve_path(cfg, "raw")
    out_path = raw_dir / "cbeci.parquet"
    max_gap = cfg["fetch"]["max_gap_days"]

    if out_path.exists() and not force_download:
        existing = load_parquet(out_path)
        last = pd.to_datetime(existing["date"]).max()
        age_days = (pd.Timestamp.utcnow().tz_localize(None) - last).days
        if age_days <= 7:
            logger.info("Using cached CBECI (last date %s, age %d d)", last.date(), age_days)
            assert_max_gap(existing, "date", max_gap, "cbeci")
            return existing
        logger.info("CBECI cache stale (age %d d); refreshing", age_days)

    # Prefer freshest download; fall back to manual
    try:
        df = download_cbeci(cfg)
    except Exception as exc:
        logger.warning("Download failed (%s); trying manual", exc)
        df = load_manual_cbeci(cfg)
        if df is None:
            raise

    df["fetched_at"] = utc_now_iso()
    assert_max_gap(df[df["date"] >= "2013-01-01"], "date", max_gap, "cbeci")
    save_parquet(df, out_path)
    return df


if __name__ == "__main__":
    setup_logging()
    d = fetch_cbeci(force_download=True)
    print(d.tail())
    print(d.describe())
