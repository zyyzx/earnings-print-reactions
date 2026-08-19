"""Secondary / bootstrap source: Yahoo Finance via yfinance.

  python -m src.ingest_yahoo --all            # prices (adj close) + earnings dates/EPS for the universe
  python -m src.ingest_yahoo --tickers HAL BKR

Use: validate the pipeline before the CapIQ workbook is refreshed, and cross-check CapIQ
announce dates / timing. Rows are tagged source='yahoo'. CapIQ ingest (source='ciq') overwrites
the same (ticker, fq_label) keys when run later — pass --no-clobber-ciq to keep ciq rows.
Timing: Yahoo earnings timestamps are in US/Eastern; hour < 12 -> BMO, else AMC.
"""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd
import yfinance as yf

from . import db, labels
from .paths import BENCHMARKS_CSV, UNIVERSE_CSV


def prices_for(ticker: str, years: int = 11) -> pd.DataFrame:
    h = yf.Ticker(ticker).history(period=f"{years}y", auto_adjust=False)
    if h.empty:
        return pd.DataFrame(columns=["ticker", "date", "close", "source"])
    idx = pd.to_datetime(h.index).tz_localize(None).normalize()
    return pd.DataFrame({"ticker": ticker, "date": idx, "close": h["Adj Close"].values, "source": "yahoo"})


def earnings_for(ticker: str, fye_month: int = 12, n_quarters: int = 42, start_fq: str = "1Q16"):
    ed = yf.Ticker(ticker).get_earnings_dates(limit=90)
    if ed is None or ed.empty:
        return pd.DataFrame(), {}
    ed = ed.copy()
    ed.index = pd.to_datetime(ed.index)
    ed = ed[~ed.index.duplicated(keep="first")].sort_index()
    ed["ts_local"] = ed.index.tz_convert("US/Eastern") if ed.index.tz is not None else ed.index
    ed["timing"] = ["BMO" if t.hour < 12 else "AMC" for t in ed["ts_local"]]
    ed["announce"] = [pd.Timestamp(t.date()) for t in ed["ts_local"]]
    today = pd.Timestamp(date.today())
    past = ed[(ed["announce"] < today) & ed["Reported EPS"].notna()].tail(n_quarters)
    # drop anything before start_fq (labels are sortable tuples)
    past = past[[labels.sort_key(labels.label_from_date_guess(a, fye_month)) >= labels.sort_key(labels.normalise(start_fq)) for a in past["announce"]]]
    fut = ed[(ed["announce"] >= today)].head(1)
    recs, seen = [], set()
    for i, (ts, r) in enumerate(past.iterrows()):
        lab = labels.label_from_date_guess(r["announce"], fye_month)
        if lab in seen:            # two events guessed into the same quarter (rare) — keep the later one
            recs = [x for x in recs if x["fq_label"] != lab]
        seen.add(lab)
        est, act, sp = r.get("EPS Estimate"), r.get("Reported EPS"), r.get("Surprise(%)")
        recs.append(dict(ticker=ticker, fq_label=lab, offset=None, announce_date=r["announce"], period_end=None,
                         eps_est=None if pd.isna(est) else float(est), eps_est_preprint=None if pd.isna(est) else float(est),
                         eps_actual=None if pd.isna(act) else float(act),
                         eps_surprise_pct=None if pd.isna(sp) else float(sp), eps_num_est=None,
                         timing=r["timing"], source="yahoo"))
    n = len(recs)
    for k, rec in enumerate(recs):
        rec["offset"] = k - n + 1
    fwd = {}
    if not fut.empty:
        r = fut.iloc[0]
        est = r.get("EPS Estimate")
        fwd = dict(ticker=ticker, asof=today.strftime("%Y-%m-%d"),
                   next_fq_label=labels.label_from_date_guess(r["announce"], fye_month),
                   next_announce_date=r["announce"].strftime("%Y-%m-%d"), next_period_end=None,
                   next_eps_est_now=None if pd.isna(est) else float(est), next_eps_est_m28d=None,
                   next_eps_num_est=None, source="yahoo")
    return pd.DataFrame(recs), fwd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-clobber-ciq", action="store_true", help="do not overwrite rows whose source is 'ciq'")
    args = ap.parse_args()
    uni = pd.read_csv(UNIVERSE_CSV).set_index("ticker")
    bench = pd.read_csv(BENCHMARKS_CSV)
    tickers = list(uni.index) if args.all or not args.tickers else args.tickers
    con = db.connect()
    existing_ciq = set()
    if args.no_clobber_ciq:
        existing_ciq = {(r[0], r[1]) for r in con.execute("SELECT ticker, fq_label FROM earnings WHERE source='ciq'")}
    needed_bench = sorted({uni.loc[t, "benchmark"] for t in tickers if t in uni.index} | set(bench.ticker))
    for inst in tickers + needed_bench:
        px = prices_for(inst)
        n = db.upsert(con, "prices", px, ["ticker", "date"])
        db.log(con, "yahoo", "prices", n, 0, inst)
        print(f"  prices {inst:6s}: {n} rows")
    for t in tickers:
        if t not in uni.index:
            print(f"  {t}: not in universe.csv — skipped")
            continue
        earn, fwd = earnings_for(t, int(uni.loc[t, "fye_month"]))
        if args.no_clobber_ciq and not earn.empty:
            earn = earn[[(t, l) not in existing_ciq for l in earn.fq_label]]
        n = db.upsert(con, "earnings", earn, ["ticker", "fq_label"])
        if fwd:
            db.upsert(con, "forward", pd.DataFrame([fwd]), ["ticker"])
        db.log(con, "yahoo", "earnings", n, 0, t)
        tim = earn.timing.value_counts().to_dict() if not earn.empty else {}
        print(f"  earnings {t:5s}: {n} quarters, timing {tim}, next {fwd.get('next_announce_date')}")
    print("Done ->", db.DB_PATH)


if __name__ == "__main__":
    main()
