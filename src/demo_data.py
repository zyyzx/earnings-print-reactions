"""Synthetic data so the pipeline can be exercised before real CapIQ data is refreshed.
Writes to a separate DB (data/demo.sqlite) — never touches data/db.sqlite.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db, labels
from .paths import DATA, UNIVERSE_CSV, BENCHMARKS_CSV

DEMO_DB = DATA / "demo.sqlite"


def make(seed: int = 7, years: int = 5):
    rng = np.random.default_rng(seed)
    uni = pd.read_csv(UNIVERSE_CSV)
    bench = pd.read_csv(BENCHMARKS_CSV)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=252 * years)
    con = db.connect(DEMO_DB)
    con.executescript("DELETE FROM prices; DELETE FROM earnings; DELETE FROM forward;")
    # benchmarks
    bench_ret = {}
    for b in bench.ticker:
        r = rng.normal(0.0003, 0.013, len(dates))
        bench_ret[b] = r
        px = 100 * np.cumprod(1 + r)
        db.upsert(con, "prices", pd.DataFrame({"ticker": b, "date": dates, "close": px, "source": "demo"}), ["ticker", "date"])
    # stocks with earnings events every ~63 trading days
    for row in uni.itertuples(index=False):
        beta = rng.uniform(0.8, 1.4)
        idio = rng.normal(0, 0.012, len(dates))
        r = beta * bench_ret[row.benchmark] + idio
        # earnings events: ~25 days after each calendar quarter end (deterministic labels)
        qends = pd.date_range(dates[0], dates[-1], freq="QE")
        event_idx = []
        for qe in qends:
            ann = qe + pd.Timedelta(days=int(rng.integers(20, 30)))
            pos = dates.searchsorted(ann)
            if 2 <= pos < len(dates) - 3:
                event_idx.append(pos)
        recs = []
        for i in event_idx:
            jump = rng.normal(0.002, 0.045)                    # earnings-day idio move
            pre = rng.normal(0.004, 0.012)                     # day-before drift (positioning)
            timing = row.timing_default
            t0 = i if timing == "BMO" else i + 1
            r[t0] += jump
            r[t0 - 1] += pre
            r[t0 + 1] += rng.normal(0, 0.01)
            ann = dates[i]
            lab = labels.label_from_date_guess(ann)
            est = 0.5 + 0.02 * len(recs) + rng.normal(0, 0.03)
            act = est * (1 + rng.normal(0.03, 0.06))
            recs.append(dict(ticker=row.ticker, fq_label=lab, offset=len(recs) - len(event_idx) + 1,
                             announce_date=ann, period_end=ann - pd.Timedelta(days=25),
                             eps_est=est, eps_est_preprint=est, eps_actual=act,
                             eps_surprise_pct=(act - est) / abs(est) * 100, eps_num_est=25,
                             timing=timing, source="demo"))
        px = 50 * np.cumprod(1 + r)
        db.upsert(con, "prices", pd.DataFrame({"ticker": row.ticker, "date": dates, "close": px, "source": "demo"}), ["ticker", "date"])
        db.upsert(con, "earnings", pd.DataFrame(recs), ["ticker", "fq_label"])
        last = recs[-1]
        nxt = last["announce_date"] + pd.Timedelta(days=91)
        db.upsert(con, "forward", pd.DataFrame([dict(
            ticker=row.ticker, asof=pd.Timestamp.today().strftime("%Y-%m-%d"),
            next_fq_label=labels.label_from_date_guess(nxt), next_announce_date=nxt.strftime("%Y-%m-%d"),
            next_period_end=None, next_eps_est_now=last["eps_est"] * 1.05,
            next_eps_est_m28d=last["eps_est"] * 1.05 * (1 + rng.normal(0, 0.01)), next_eps_num_est=25, source="demo")]), ["ticker"])
    con.close()
    return DEMO_DB


if __name__ == "__main__":
    print("demo db ->", make())
