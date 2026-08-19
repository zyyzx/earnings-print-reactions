import numpy as np
import pandas as pd
import pytest

from src import compute, labels


def _prices():
    # 10 business days; stock and bench with known daily moves
    dates = pd.bdate_range("2024-01-01", periods=10)
    stock = [100, 101, 101, 103.02, 103.02, 100, 100, 100, 100, 100]      # +1%, 0, +2%, 0, -2.93%...
    bench = [50, 50, 50.5, 50.5, 50.5, 50, 50, 50, 50, 50]                # 0, +1%, 0, 0, -0.99%
    rows = [("S", d, p) for d, p in zip(dates, stock)] + [("B", d, p) for d, p in zip(dates, bench)]
    return pd.DataFrame(rows, columns=["ticker", "date", "close"]), dates


def test_bmo_event_windows():
    prices, dates = _prices()
    earn = pd.DataFrame([dict(ticker="S", fq_label="1Q24", announce_date=dates[3], timing="BMO")])
    ev = compute.event_returns(prices, earn, "S", "B")
    r = ev.iloc[0]
    assert r.t0 == dates[3]
    assert r.tm1_date == dates[2] and r.tp1_date == dates[4]
    assert r.t0_abs == pytest.approx(200, abs=1e-6)          # 101 -> 103.02
    assert r.t0_bench == pytest.approx(0, abs=1e-6)
    assert r.t0_rel == pytest.approx(200, abs=1e-6)
    assert r.tm1_abs == pytest.approx(0, abs=1e-6)
    assert r.tm1_rel == pytest.approx(-100, abs=1e-6)        # bench +1% on t-1


def test_amc_shifts_t0_to_next_session():
    prices, dates = _prices()
    earn = pd.DataFrame([dict(ticker="S", fq_label="1Q24", announce_date=dates[2], timing="AMC")])
    ev = compute.event_returns(prices, earn, "S", "B")
    assert ev.iloc[0].t0 == dates[3]


def test_weekend_announce_rolls_forward():
    prices, dates = _prices()
    sat = dates[4] + pd.Timedelta(days=2)     # Saturday
    earn = pd.DataFrame([dict(ticker="S", fq_label="2Q24", announce_date=sat, timing="BMO")])
    ev = compute.event_returns(prices, earn, "S", "B")
    assert ev.iloc[0].t0 == dates[5]


def test_timing_override_applies():
    prices, dates = _prices()
    earn = pd.DataFrame([dict(ticker="S", fq_label="1Q24", announce_date=dates[2], timing="BMO")])
    ov = pd.DataFrame([dict(ticker="S", fq="1Q24", timing="AMC")])
    ev = compute.event_returns(prices, earn, "S", "B", timing_overrides=ov)
    assert ev.iloc[0].t0 == dates[3]


def test_panel_stats_and_follow_through():
    ev = pd.DataFrame({
        "fq_label": ["1Q23", "2Q23", "3Q23", "4Q23"],
        "tm1_rel": [150, -50, 120, 10], "t0_rel": [80, -200, -30, 40], "tp1_rel": [5, 5, -5, 0],
        "tm1_abs": [0, 0, 0, 0], "t0_abs": [100, -250, -10, 60], "tp1_abs": [0, 0, 0, 0],
        "timing_flag": [False] * 4,
    })
    ps = compute.panel_stats(ev)
    assert ps.n_events == 4 and ps.outperform == 2 and ps.underperform == 2
    assert ps.range.loc["t0", "high"] == 80 and ps.range.loc["t0", "low"] == -200
    assert ps.range.loc["tm1", "avg"] == pytest.approx(57.5)
    assert list(ps.follow_through.fq_label) == ["1Q23", "3Q23"]
    assert ps.hit_rate_follow_through == pytest.approx(0.5)


def test_header_stats():
    ev = pd.DataFrame({"fq_label": ["1Q23"], "t0_abs": [-350.0]})
    earn = pd.DataFrame([dict(ticker="S", fq_label="1Q23", announce_date=pd.Timestamp("2023-04-25"),
                              eps_est=0.67, eps_est_preprint=0.67, eps_actual=0.72, eps_surprise_pct=None)])
    fwd = dict(next_announce_date="2023-07-19", next_eps_est_now=0.75, next_eps_est_m28d=0.753)
    h = compute.header_stats(ev, earn, fwd)
    assert h["last_q_label"] == "1Q23"
    assert h["last_q_eps_day_perf"] == pytest.approx(-0.035)
    assert h["eps_surprise_last_q"] == pytest.approx((0.72 - 0.67) / 0.67)
    assert h["eps_4wk_change"] == pytest.approx(0.75 / 0.753 - 1)


def test_labels():
    assert labels.normalise("2025FQ2") == "2Q25"
    assert labels.normalise("FQ22025") == "2Q25"
    assert labels.normalise("1QFY-2023") == "1Q23"
    assert labels.normalise("3Q24") == "3Q24"
    assert labels.from_period_end(pd.Timestamp("2023-03-31"), 12) == "1Q23"
    assert labels.from_period_end(pd.Timestamp("2023-12-31"), 12) == "4Q23"
    assert labels.from_period_end(pd.Timestamp("2023-08-31"), 5) == "1Q24"   # May FYE: Jun-Aug is 1Q of FY24
    assert labels.label_from_date_guess(pd.Timestamp("2023-04-25")) == "1Q23"
    assert labels.label_from_date_guess(pd.Timestamp("2024-01-23")) == "4Q23"
    assert labels.sort_key("4Q23") < labels.sort_key("1Q24")
