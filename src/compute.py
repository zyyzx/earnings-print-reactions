"""Event-window math: t-1 / t0 / t+1 absolute and relative returns (bps) per earnings event.

Definitions (see plan):
  t0  = first full trading session reflecting the print (BMO -> announce day; AMC -> next trading day)
  t-1 / t+1 = trading day before / after t0 (on the stock's own price calendar)
  abs bps = stock close-to-close return * 1e4 ; rel bps = (stock - benchmark) * 1e4
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import labels

WINDOWS = ("tm1", "t0", "tp1")
FOLLOW_THROUGH_BPS = 100.0


def close_series(prices: pd.DataFrame, ticker: str) -> pd.Series:
    s = prices.loc[prices.ticker == ticker].set_index("date")["close"].sort_index()
    return s[~s.index.duplicated(keep="last")].astype(float)


def daily_returns(close: pd.Series) -> pd.Series:
    return close.pct_change()


def resolve_t0(announce, timing: str | None, calendar: pd.DatetimeIndex):
    """Trading day whose close first reflects the release. Unknown timing -> treated as BMO."""
    if announce is None or pd.isna(announce):
        return pd.NaT
    a = pd.Timestamp(announce).normalize()
    tim = (timing or "BMO").upper()
    if tim == "AMC":
        pos = calendar.searchsorted(a, side="right")      # strictly after
    else:
        pos = calendar.searchsorted(a, side="left")       # on or after
    return calendar[pos] if pos < len(calendar) else pd.NaT


def benchmark_returns(prices: pd.DataFrame, benchmarks: list[str]) -> pd.Series:
    """Daily benchmark return; if the primary benchmark has no return on a date, fall back
    to the next benchmark in the list (e.g. OIH -> XLE -> SPY) so a data gap never drops an event."""
    out = None
    for b in benchmarks:
        s = prices.loc[prices.ticker == b]
        if s.empty:
            continue
        r = daily_returns(close_series(prices, b))
        out = r if out is None else out.combine_first(r)
    return out if out is not None else pd.Series(dtype=float)


def peer_basket_returns(prices: pd.DataFrame, members: list[str], exclude: str | None = None) -> pd.Series:
    """Equal-weight daily return of the peer basket (ex the stock itself), averaging whatever members have data."""
    cols = {}
    for m in members:
        if m == exclude or prices.loc[prices.ticker == m].empty:
            continue
        cols[m] = daily_returns(close_series(prices, m))
    if not cols:
        return pd.Series(dtype=float)
    return pd.DataFrame(cols).mean(axis=1, skipna=True)


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """Wilson score interval (default 90%) for a hit rate k/n."""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def event_returns(prices: pd.DataFrame, earnings: pd.DataFrame, ticker: str, benchmark,
                  timing_overrides: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per earnings event with t-1/t0/t+1 abs & rel returns in bps.
    `benchmark` may be a ticker, a list of tickers (first primary, rest gap fallbacks),
    or a pd.Series of daily benchmark returns (e.g. a peer basket)."""
    cs = close_series(prices, ticker)
    rs = daily_returns(cs)
    if isinstance(benchmark, pd.Series):
        rb = benchmark
    else:
        benches = [benchmark] if isinstance(benchmark, str) else list(benchmark)
        rb = benchmark_returns(prices, benches)
    cal = cs.index
    ov = {}
    if timing_overrides is not None and not timing_overrides.empty:
        sub = timing_overrides[timing_overrides.ticker == ticker]
        ov = dict(zip(sub.fq, sub.timing))
    rows = []
    for e in earnings.itertuples(index=False):
        timing = ov.get(e.fq_label, getattr(e, "timing", None))
        t0 = resolve_t0(e.announce_date, timing, cal)
        rec = dict(fq_label=e.fq_label, announce_date=pd.Timestamp(e.announce_date), timing=timing, t0=t0)
        if pd.isna(t0):
            rows.append(rec)
            continue
        i = cal.get_loc(t0)
        for w, k in zip(WINDOWS, (-1, 0, 1)):
            j = i + k
            d = cal[j] if 0 <= j < len(cal) else pd.NaT
            rec[f"{w}_date"] = d
            a = rs.get(d, np.nan) if not pd.isna(d) else np.nan
            b = rb.get(d, np.nan) if not pd.isna(d) else np.nan
            rec[f"{w}_abs"] = a * 1e4
            rec[f"{w}_bench"] = b * 1e4
            rec[f"{w}_rel"] = (a - b) * 1e4
        # sanity flag: BMO-tagged but next day moved much more than t0 -> maybe AMC
        j = i + 1
        if j < len(cal) and (timing or "BMO").upper() == "BMO":
            nxt = abs(rs.get(cal[j], np.nan) - rb.get(cal[j], np.nan))
            cur = abs(rs.get(t0, np.nan) - rb.get(t0, np.nan))
            # flag only when t0 was quiet and t+1 was a big move (looks like an AMC print tagged BMO)
            rec["timing_flag"] = bool(np.isfinite(nxt) and np.isfinite(cur) and cur < 0.01 and nxt > 0.04)
        else:
            rec["timing_flag"] = False
        rows.append(rec)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["sort"] = df.fq_label.map(labels.sort_key)
    return df.sort_values("sort").drop(columns="sort").reset_index(drop=True)


@dataclass
class PanelStats:
    n_events: int
    outperform: int
    underperform: int
    range: pd.DataFrame            # index tm1/t0/tp1, cols high/low/avg (relative bps)
    follow_through: pd.DataFrame   # events with tm1_rel > 100: fq_label, t0_rel, t0_abs
    hit_rate_follow_through: float | None
    avg_abs_move: dict = field(default_factory=dict)  # mean |rel| per window
    outperform_ci: tuple = (np.nan, np.nan)           # Wilson 90% band on outperform share


def panel_stats(ev: pd.DataFrame) -> PanelStats:
    valid = ev.dropna(subset=["t0_rel"]) if "t0_rel" in ev else ev.iloc[0:0]
    outp = int((valid.t0_rel > 0).sum())
    under = int((valid.t0_rel <= 0).sum())
    rng = pd.DataFrame({
        "high": [ev[f"{w}_rel"].max() if f"{w}_rel" in ev else np.nan for w in WINDOWS],
        "low": [ev[f"{w}_rel"].min() if f"{w}_rel" in ev else np.nan for w in WINDOWS],
        "avg": [ev[f"{w}_rel"].mean() if f"{w}_rel" in ev else np.nan for w in WINDOWS],
    }, index=list(WINDOWS))
    ft = ev[(ev.get("tm1_rel", pd.Series(dtype=float)) > FOLLOW_THROUGH_BPS)][["fq_label", "t0_rel", "t0_abs"]] \
        if "tm1_rel" in ev else pd.DataFrame(columns=["fq_label", "t0_rel", "t0_abs"])
    hit = float((ft.t0_rel > 0).mean()) if len(ft) else None
    avg_abs = {w: float(ev[f"{w}_rel"].abs().mean()) if f"{w}_rel" in ev else np.nan for w in WINDOWS}
    ps = PanelStats(len(valid), outp, under, rng, ft.reset_index(drop=True), hit, avg_abs)
    ps.outperform_ci = wilson(outp, len(valid))
    return ps


def header_stats(ev: pd.DataFrame, earnings: pd.DataFrame, forward: dict) -> dict:
    """The six header boxes of the original dashboard."""
    last = earnings.dropna(subset=["announce_date"]).sort_values("announce_date").tail(1)
    h = {"ticker": None, "next_call": forward.get("next_announce_date"),
         "implied_move": forward.get("implied_move"),
         "eps_4wk_change": None, "eps_surprise_last_q": None,
         "last_q_label": None, "last_q_eps_day_perf": None}
    now, m28 = forward.get("next_eps_est_now"), forward.get("next_eps_est_m28d")
    if now is not None and m28 not in (None, 0) and not pd.isna(now) and not pd.isna(m28):
        h["eps_4wk_change"] = float(now) / float(m28) - 1.0
    if not last.empty:
        row = last.iloc[0]
        h["last_q_label"] = row.fq_label
        sp = row.get("eps_surprise_pct")
        if sp is not None and not pd.isna(sp):
            h["eps_surprise_last_q"] = float(sp) / 100.0
        else:
            est = row.get("eps_est_preprint") if not pd.isna(row.get("eps_est_preprint")) else row.get("eps_est")
            act = row.get("eps_actual")
            if est not in (None, 0) and act is not None and not pd.isna(est) and not pd.isna(act):
                h["eps_surprise_last_q"] = (act - est) / abs(est)
        m = ev[ev.fq_label == row.fq_label]
        if not m.empty and "t0_abs" in m and not pd.isna(m.iloc[0].t0_abs):
            h["last_q_eps_day_perf"] = float(m.iloc[0].t0_abs) / 1e4
    return h


def header_stats_hist(ev: pd.DataFrame, earnings: pd.DataFrame, ps: PanelStats) -> dict:
    """Historical-only header boxes for the PM draft (no forward-looking fields)."""
    eps = eps_history(earnings)
    ok = eps.dropna(subset=["eps_actual", "consensus"])
    beats = int((ok.eps_actual > ok.consensus).sum()); misses = int((ok.eps_actual < ok.consensus).sum())
    inline = int(len(ok) - beats - misses)
    last = ev.dropna(subset=["t0_abs"]).sort_values("t0").tail(1)
    h = {"n_prints": ps.n_events, "outperform": ps.outperform, "outperform_ci": ps.outperform_ci,
         "avg_abs_t0_rel": ps.avg_abs_move.get("t0"), "avg_abs_t0_abs": float(ev["t0_abs"].abs().mean()) if "t0_abs" in ev else np.nan,
         "beats": beats, "misses": misses, "inline": inline, "n_eps": int(len(ok)),
         "last_q_label": None, "last_date": None, "last_surprise": None, "last_t0_abs": None, "last_t0_rel": None}
    if not last.empty:
        r = last.iloc[0]
        h.update(last_q_label=r.fq_label, last_date=pd.Timestamp(r.t0).strftime("%Y-%m-%d"),
                 last_t0_abs=float(r.t0_abs) / 1e4, last_t0_rel=float(r.t0_rel) / 1e4 if not pd.isna(r.t0_rel) else None)
        m = earnings[earnings.fq_label == r.fq_label]
        if not m.empty:
            row = m.iloc[0]
            sp = row.get("eps_surprise_pct")
            if sp is not None and not pd.isna(sp):
                h["last_surprise"] = float(sp) / 100
            else:
                est = row.get("eps_est_preprint") if not pd.isna(row.get("eps_est_preprint")) else row.get("eps_est")
                if est not in (None, 0) and not pd.isna(est) and not pd.isna(row.get("eps_actual")):
                    h["last_surprise"] = (row.eps_actual - est) / abs(est)
    return h


def eps_history(earnings: pd.DataFrame) -> pd.DataFrame:
    """Reported vs consensus per quarter for the bonus chart. Prefers pre-print consensus."""
    df = earnings.copy()
    df["consensus"] = df["eps_est_preprint"].where(df["eps_est_preprint"].notna(), df["eps_est"])
    df["sort"] = df.fq_label.map(labels.sort_key)
    return df.sort_values("sort")[["fq_label", "eps_actual", "consensus", "eps_surprise_pct"]].reset_index(drop=True)


def universe_summary(results: dict[str, tuple[pd.DataFrame, PanelStats]]) -> pd.DataFrame:
    rows = []
    for t, (ev, ps) in results.items():
        rows.append(dict(
            ticker=t, n_events=ps.n_events,
            outperform_pct=(ps.outperform / ps.n_events) if ps.n_events else np.nan,
            avg_rel_tm1=ps.range.loc["tm1", "avg"], avg_rel_t0=ps.range.loc["t0", "avg"], avg_rel_tp1=ps.range.loc["tp1", "avg"],
            avg_abs_rel_tm1=ps.avg_abs_move["tm1"], avg_abs_rel_t0=ps.avg_abs_move["t0"], avg_abs_rel_tp1=ps.avg_abs_move["tp1"],
            follow_through_n=len(ps.follow_through), follow_through_hit=ps.hit_rate_follow_through,
            n_timing_flags=int(ev.timing_flag.sum()) if "timing_flag" in ev else 0,
        ))
    return pd.DataFrame(rows)
