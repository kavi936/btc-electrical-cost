"""Halving schedule and subsidy checks."""

from __future__ import annotations

import pandas as pd
import pytest

from src.config_loader import load_config
from src.fetch_coinmetrics import subsidy_on_date


@pytest.fixture
def schedule():
    return load_config()["halving"]["schedule"]


def test_pre_first_halving(schedule):
    assert subsidy_on_date(pd.Timestamp("2011-01-01"), schedule) == 50.0


def test_after_2012_halving(schedule):
    assert subsidy_on_date(pd.Timestamp("2012-11-28"), schedule) == 25.0
    assert subsidy_on_date(pd.Timestamp("2012-11-27"), schedule) == 50.0


def test_after_2016_halving(schedule):
    assert subsidy_on_date(pd.Timestamp("2016-07-09"), schedule) == 12.5


def test_after_2020_halving(schedule):
    assert subsidy_on_date(pd.Timestamp("2020-05-11"), schedule) == 6.25


def test_after_2024_halving(schedule):
    assert subsidy_on_date(pd.Timestamp("2024-04-20"), schedule) == 3.125
    assert subsidy_on_date(pd.Timestamp("2026-01-01"), schedule) == 3.125


def test_halves_each_era(schedule):
    subsidies = [row["subsidy"] for row in schedule]
    for a, b in zip(subsidies, subsidies[1:]):
        assert b == pytest.approx(a / 2.0)
