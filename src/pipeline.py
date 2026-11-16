"""End-to-end pipeline: fetch → model → test → report."""

from __future__ import annotations

import logging
import sys

from src.config_loader import load_config
from src.fetch_cbeci import fetch_cbeci
from src.fetch_coinmetrics import fetch_coinmetrics
from src.fetch_ohlc import fetch_all_ohlc
from src.fetch_utils import setup_logging
from src.models import build_all_costs, model_a_current_estimate
from src.report import build_report
from src.tests_claim import run_all_tests

logger = logging.getLogger(__name__)


def run(*, force_cbeci: bool = False) -> dict:
    setup_logging()
    cfg = load_config()

    logger.info("=== 1/5 Fetch OHLC ===")
    ohlc = fetch_all_ohlc(cfg)

    logger.info("=== 2/5 Fetch network metrics ===")
    network = fetch_coinmetrics(cfg)

    logger.info("=== 3/5 Fetch CBECI ===")
    cbeci = fetch_cbeci(cfg, force_download=force_cbeci)

    logger.info("=== 4/5 Build cost models ===")
    costs = build_all_costs(cbeci, network, cfg)

    # Sanity: Model A recent level should be ~$45–60K at $0.05/kWh
    est = model_a_current_estimate(cbeci, network, cfg)
    lo = cfg["tests"]["model_a_sanity_low"]
    hi = cfg["tests"]["model_a_sanity_high"]
    guess = est["cost_A_guess"]
    logger.info(
        "Model A sanity: date=%s cost_A_guess=$%.0f (expected $%.0f–$%.0f)",
        est["date"],
        guess,
        lo,
        hi,
    )
    if not (lo <= guess <= hi):
        logger.error(
            "SANITY CHECK FAILED: Model A guess $%.0f outside $%.0f–$%.0f. "
            "Investigate TWh, rewards, or $/kWh before trusting the report.",
            guess,
            lo,
            hi,
        )
        # Continue — still produce report, but flag loudly
    else:
        logger.info("Sanity check OK")

    logger.info("=== 5/5 Tests + report ===")
    results = run_all_tests(ohlc, costs, cbeci, network, cfg)
    path = build_report(ohlc, costs, results, cfg)

    print("\n" + "=" * 60)
    print("VERDICT:", "HOLDS" if results["verdict"]["holds"] else "FALSE")
    print(results["verdict"]["evidence"])
    print(f"Report: {path}")
    print(f"Model A guess (latest): ${guess:,.0f} on {est['date']}")
    print("=" * 60)
    return {"ohlc": ohlc, "costs": costs, "results": results, "estimate": est, "report": path}


if __name__ == "__main__":
    force = "--force-cbeci" in sys.argv
    run(force_cbeci=force)
