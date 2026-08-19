"""Build dashboards.

  python -m src.build --tickers HAL BKR      # from data/db.sqlite (original header, ETF benchmark)
  python -m src.build --all
  python -m src.build --all --demo           # synthetic data (data/demo.sqlite) to preview the layout
  python -m src.build --all --pm             # PM draft: peer-basket relative, historical-only header,
                                             # cover page + single PDF -> output/pm_draft/
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

from . import compute, db, render
from .paths import DB_PATH, OUTPUT, TIMING_OVERRIDES_CSV, UNIVERSE_CSV

BENCH_FALLBACKS = ["XLE", "SPY"]      # used only when the primary benchmark has a price gap on a window day


def build_ticker(con, ticker: str, uni: pd.DataFrame, overrides: pd.DataFrame, out_dir: Path,
                 note: str = "", pm: bool = False, pdf=None):
    uni_row = uni.loc[ticker]
    bench = uni_row["benchmark"]
    chain = [bench] + [b for b in BENCH_FALLBACKS if b != bench]
    peers = [t for t in uni.index[uni.peer_group == uni_row.get("peer_group")]] if "peer_group" in uni else []
    prices = db.read_prices(con, [ticker] + chain + peers)
    earn = db.read_earnings(con, ticker)
    if earn.empty or prices[prices.ticker == ticker].empty:
        print(f"  {ticker}: no data (earnings rows={len(earn)}, price rows={len(prices[prices.ticker == ticker])}) — skipped")
        return None
    fwd = db.read_forward(con, ticker)
    im = uni_row.get("implied_move")
    if im is not None and not pd.isna(im):
        fwd["implied_move"] = float(im)

    # benchmark: PM mode = equal-weight peer basket ex-self (ETF chain as gap fallback); else ETF chain
    bench_label = bench
    if pm and len(peers) > 1:
        basket = compute.peer_basket_returns(prices, peers, exclude=ticker)
        etf = compute.benchmark_returns(prices, chain)
        rb = basket.combine_first(etf)
        bench_label = f"{uni_row.get('peer_group')} peers"
        ev = compute.event_returns(prices, earn, ticker, rb, overrides)
        ev_etf = compute.event_returns(prices, earn, ticker, chain, overrides)
    else:
        ev = compute.event_returns(prices, earn, ticker, chain, overrides)
        ev_etf = ev
    ps = compute.panel_stats(ev)
    eps = compute.eps_history(earn)
    if pm:
        header = compute.header_stats_hist(ev, earn, ps)
        header["first_label"] = ev.fq_label.iloc[0] if len(ev) else "n/a"
    else:
        header = compute.header_stats(ev, earn, fwd)
    png = out_dir / f"{ticker}.png"
    render.render_dashboard(ticker, ev, ps, header, eps, bench_label, png, note=note,
                            mode="hist" if pm else "full", pdf=pdf)
    ev.to_csv(out_dir / f"{ticker}_events.csv", index=False)
    flags = ev.loc[ev.timing_flag, "fq_label"].tolist() if "timing_flag" in ev else []
    msg = f"  {ticker}: {ps.n_events} events, outperform {ps.outperform}/{ps.n_events} vs {bench_label} -> {png.name}"
    if flags:
        msg += f"   [timing check: {', '.join(flags)}]"
    print(msg)
    ps_etf = compute.panel_stats(ev_etf)
    return ev, ps, {"header": header, "ps_etf": ps_etf, "bench_label": bench_label, "peers": [p for p in peers if p != ticker]}


def summarise(results: dict) -> pd.DataFrame:
    base = compute.universe_summary({t: (r[0], r[1]) for t, r in results.items()})
    extra = []
    for t, (ev, ps, meta) in results.items():
        h = meta["header"]
        lo, hi = ps.outperform_ci
        extra.append(dict(ticker=t, ci_lo=lo, ci_hi=hi,
                          beat_rate=(h.get("beats", 0) / h["n_eps"]) if h.get("n_eps") else np.nan,
                          outperform_pct_etf=(meta["ps_etf"].outperform / meta["ps_etf"].n_events) if meta["ps_etf"].n_events else np.nan,
                          avg_abs_t0_abs=h.get("avg_abs_t0_abs"), bench=meta["bench_label"]))
    return base.merge(pd.DataFrame(extra), on="ticker")


def observations(summary: pd.DataFrame, results: dict, uni: pd.DataFrame) -> list[str]:
    """Short, trading-oriented bullets generated from the numbers (every figure traceable to summary.csv)."""
    s = summary.set_index("ticker")
    b = []
    mean_t0, mean_tm1, mean_tp1 = s.avg_abs_rel_t0.mean(), s.avg_abs_rel_tm1.mean(), s.avg_abs_rel_tp1.mean()
    b.append(f"The print day carries the move: {mean_t0:,.0f} bps avg vs peers on the day, vs {mean_tm1:,.0f} the day before and {mean_tp1:,.0f} the day after. "
             f"Day-after drift nets to {s.avg_rel_tp1.mean():+.0f} bps — the reaction is done by the close.")
    worst = s.outperform_pct.idxmin(); best = s.outperform_pct.idxmax()
    b.append(f"Beating the number is not beating peers: {worst} beat consensus {s.loc[worst, 'beat_rate']:.0%} of the time but was up vs peers on only "
             f"{s.loc[worst, 'outperform_pct']:.0%} of print days; {best} is the only name above 60% ({s.loc[best, 'outperform_pct']:.0%}).")
    top = s.avg_abs_rel_t0.sort_values(ascending=False)
    b.append(f"Where the juice is: {top.index[0]} {top.iloc[0]:,.0f} bps avg print-day move vs peers, {top.index[1]} {top.iloc[1]:,.0f}, {top.index[2]} {top.iloc[2]:,.0f}; "
             f"{top.index[-1]} the quietest at {top.iloc[-1]:,.0f}.")
    ft = s[s.follow_through_n >= 3]
    if len(ft):
        lo = ft.follow_through_hit.idxmin(); hi = ft.follow_through_hit.idxmax()
        b.append(f"A rally into the print is not a signal: follow-through on the day ranges from {ft.loc[lo, 'follow_through_hit']:.0%} ({lo}, n={int(ft.loc[lo, 'follow_through_n'])}) "
                 f"to {ft.loc[hi, 'follow_through_hit']:.0%} ({hi}, n={int(ft.loc[hi, 'follow_through_n'])}).")
    return b


def write_index(out_dir: Path, tickers: list[str], summary: pd.DataFrame | None, note: str, cover: bool = False):
    rows = []
    for t in tickers:
        if (out_dir / f"{t}.png").exists():
            rows.append(f'<section id="{t}"><h2>{t}</h2><img src="{t}.png" alt="{t} earnings dashboard"></section>')
    table = ""
    if summary is not None and not summary.empty:
        s = summary.copy()
        for c in ("outperform_pct", "follow_through_hit", "beat_rate", "outperform_pct_etf", "ci_lo", "ci_hi"):
            if c in s:
                s[c] = s[c].map(lambda v: "" if pd.isna(v) else f"{v:.0%}")
        for c in ("avg_rel_tm1", "avg_rel_t0", "avg_rel_tp1", "avg_abs_rel_tm1", "avg_abs_rel_t0", "avg_abs_rel_tp1", "avg_abs_t0_abs"):
            if c in s:
                s[c] = s[c].map(lambda v: "" if pd.isna(v) else f"{v:,.0f}")
        table = s.to_html(index=False, border=0, classes="summary")
    nav = " · ".join(f'<a href="#{t}">{t}</a>' for t in tickers)
    cover_html = '<p><img src="cover.png" alt="cover"></p>' if cover else '<p><img src="cross_section.png" alt="cross section" style="max-width:600px"></p>'
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Earnings Reaction Tracker</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#333}} img{{max-width:100%;border:1px solid #ddd}}
table.summary{{border-collapse:collapse;font-size:13px}} table.summary td,table.summary th{{padding:4px 8px;border-bottom:1px solid #eee;text-align:right}}
table.summary th:first-child,table.summary td:first-child{{text-align:left}} section{{margin-top:32px}} .note{{color:#777;font-size:12px}}</style></head>
<body><h1>Earnings Reaction Tracker</h1><p class="note">{html.escape(note)}</p><p>{nav}</p>
{cover_html}
<h2>Universe summary</h2>{table}
{''.join(rows)}</body></html>"""
    (out_dir / "index.html").write_text(doc, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--demo", action="store_true", help="use synthetic data (data/demo.sqlite)")
    ap.add_argument("--pm", action="store_true", help="PM draft: peer-basket relative, historical header, cover + PDF")
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    uni = pd.read_csv(UNIVERSE_CSV).set_index("ticker")
    overrides = pd.read_csv(TIMING_OVERRIDES_CSV) if TIMING_OVERRIDES_CSV.exists() else pd.DataFrame()
    if args.demo:
        from .demo_data import make, DEMO_DB
        make()
        db_path, out_dir, note = DEMO_DB, OUTPUT / "demo", "DEMO — synthetic data, layout preview only"
    else:
        db_path, out_dir, note = Path(args.db) if args.db else DB_PATH, OUTPUT, ""
    if args.pm:
        out_dir = OUTPUT / "pm_draft"
    if args.out:
        out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tickers = list(uni.index) if args.all or not args.tickers else args.tickers
    con = db.connect(db_path)
    print(f"DB: {db_path}\nOutput: {out_dir}")
    pdf = PdfPages(out_dir / "earnings_reaction_draft.pdf") if args.pm else None
    results = {}
    # cover is page 1 of the PDF -> build tickers first, then write cover, then assemble PDF in order
    for t in tickers:
        if t not in uni.index:
            print(f"  {t}: not in universe.csv — skipped")
            continue
        r = build_ticker(con, t, uni, overrides, out_dir, note, pm=args.pm, pdf=None)
        if r is not None:
            results[t] = r
    if results:
        summary = summarise(results)
        summary.to_csv(out_dir / "summary.csv", index=False)
        render.render_cross_section(summary, out_dir / "cross_section.png")
        if args.pm:
            bullets = observations(summary, results, uni)
            first = results[list(results)[0]][0].fq_label.iloc[0]
            render.render_cover(summary, bullets, out_dir / "cover.png",
                                title="How liquid energy names trade around earnings — historical print reactions",
                                subtitle=(f"{len(summary)} names, {first} to latest print. Relative = stock minus equal-weight peer basket (ex-self), "
                                          "close-to-close, in bps. t0 = first full session reflecting the print. Source: S&P Capital IQ prices & estimates."),
                                pdf=None)
            # assemble PDF: cover first, then each dashboard (re-render figures into the pdf)
            with PdfPages(out_dir / "earnings_reaction_draft.pdf") as pdf:
                render.render_cover(summary, bullets, out_dir / "cover.png",
                                    title="How liquid energy names trade around earnings — historical print reactions",
                                    subtitle=(f"{len(summary)} names, {first} to latest print. Relative = stock minus equal-weight peer basket (ex-self), "
                                              "close-to-close, in bps. t0 = first full session reflecting the print. Source: S&P Capital IQ prices & estimates."),
                                    pdf=pdf)
                for t, (ev, ps, meta) in results.items():
                    earn = db.read_earnings(con, t)
                    render.render_dashboard(t, ev, ps, meta["header"], compute.eps_history(earn), meta["bench_label"],
                                            out_dir / f"{t}.png", note=note, mode="hist", pdf=pdf)
            (out_dir / "observations.txt").write_text("\n".join(bullets), encoding="utf-8")
            print("PDF ->", out_dir / "earnings_reaction_draft.pdf")
        write_index(out_dir, list(results), summary, note, cover=args.pm)
        print(f"Wrote {out_dir / 'index.html'} and summary.csv")


if __name__ == "__main__":
    main()
