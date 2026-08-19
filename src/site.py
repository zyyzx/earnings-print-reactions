"""Static GitHub-Pages site: docs/index.html + docs/<TICKER>.html with interactive Plotly charts.

  python -m src.site            # builds docs/ from data/db.sqlite (peer-basket relative, historical only)

Design notes: light theme, system fonts, one categorical order (t0 blue / t-1 orange / t+1 aqua), diverging
blue/red for +/- where the sign is the message, thin marks, hover tooltips, every stat shows n.
Plotly is loaded from the CDN (GitHub Pages is online anyway); pages are otherwise self-contained.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from . import compute, db
from .build import BENCH_FALLBACKS, observations, summarise
from .paths import DB_PATH, ROOT, TIMING_OVERRIDES_CSV, UNIVERSE_CSV

DOCS = ROOT / "docs"

# palette (dataviz reference instance)
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
RED, GRAY, GRID, INK, MUTED, SURFACE = "#e34948", "#9a9892", "#e6e4df", "#0b0b0b", "#52514e", "#fcfcfb"
C_T0, C_TM1, C_TP1 = BLUE, ORANGE, AQUA
C_POS, C_NEG = BLUE, RED

PLOTLY_CFG = {"displayModeBar": False, "responsive": True}

LAYOUT = dict(
    template="plotly_white", font=dict(family="Inter, Segoe UI, system-ui, -apple-system, sans-serif", size=12, color=INK),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=48, r=16, t=34, b=40), hoverlabel=dict(bgcolor="white", font_size=12),
    legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, font=dict(size=11)),
    xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=10, color=MUTED)),
    yaxis=dict(gridcolor=GRID, zerolinecolor="#c9c7c1", zerolinewidth=1, tickfont=dict(size=10, color=MUTED), hoverformat=",.0f", tickformat=",.0f"),
)
QAXIS = dict(type="category", dtick=1, tickangle=-90, tickfont=dict(size=9))   # every quarter label, incl. the last one


def rounded(ev: pd.DataFrame) -> pd.DataFrame:
    """Whole-bp copy of the event table for charting (hover shows integers)."""
    e = ev.copy()
    for c in e.columns:
        if c.endswith(("_rel", "_abs", "_bench")):
            e[c] = e[c].round(0)
    return e


def fig_html(fig: go.Figure, height: int = 360) -> str:
    title = ""
    if fig.layout.title and fig.layout.title.text:
        title = f'<h3 class="ct">{fig.layout.title.text}</h3>'
        fig.update_layout(title=None)
    fig.update_layout(**LAYOUT, height=height)
    return title + pio.to_html(fig, include_plotlyjs=False, full_html=False, config=PLOTLY_CFG, default_width="100%", default_height=f"{height}px")


def bps(v):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+,.0f}"


def pct(v, d=0):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.{d}f}%"


# ----------------------------------------------------------------------------- charts
def chart_around(ev: pd.DataFrame, bench_label: str) -> str:
    ev = rounded(ev)
    x = ev.fq_label
    lo = ev[["tm1_rel", "t0_rel", "tp1_rel"]].min(axis=1)
    hi = ev[["tm1_rel", "t0_rel", "tp1_rel"]].max(axis=1)
    fig = go.Figure()
    for xi, l, h in zip(x, lo, hi):
        fig.add_shape(type="line", x0=xi, x1=xi, y0=l, y1=h, line=dict(color="#d6d4ce", width=1))
    hv = "%{x}<br>%{fullData.name}: %{y:+,.0f} bps<extra></extra>"
    fig.add_trace(go.Scatter(x=x, y=ev.tm1_rel, mode="markers", name="t-1 (day before)", marker=dict(color=C_TM1, size=7), hovertemplate=hv))
    fig.add_trace(go.Scatter(x=x, y=ev.tp1_rel, mode="markers", name="t+1 (day after)", marker=dict(color=C_TP1, size=7, symbol="triangle-up"), hovertemplate=hv))
    fig.add_trace(go.Scatter(x=x, y=ev.t0_rel, mode="markers", name="t0 (print day)", marker=dict(color=C_T0, size=9, symbol="square", line=dict(width=1, color="white")), hovertemplate=hv))
    fig.update_layout(title=dict(text=f"Move vs {bench_label} on the day before, the print day and the day after (bps)", font=dict(size=14)),
                      yaxis_title="bps vs peers", xaxis=QAXIS)
    return fig_html(fig, 380)


def chart_t0(ev: pd.DataFrame, bench_label: str) -> str:
    ev = rounded(ev)
    cols = [C_POS if v > 0 else C_NEG for v in ev.t0_rel.fillna(0)]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=ev.fq_label, y=ev.t0_rel, name=f"relative vs {bench_label}", marker_color=cols, opacity=0.85,
                         hovertemplate="%{x}<br>relative: %{y:+,.0f} bps<extra></extra>"))
    fig.add_trace(go.Scatter(x=ev.fq_label, y=ev.t0_abs, mode="markers", name="absolute", marker=dict(color=INK, size=6, symbol="circle-open", line=dict(width=1.5)),
                             hovertemplate="%{x}<br>absolute: %{y:+,.0f} bps<extra></extra>"))
    fig.update_layout(title=dict(text="Print-day move: bars = vs peers, open circles = the stock itself (bps)", font=dict(size=14)),
                      yaxis_title="bps", xaxis=QAXIS, bargap=0.35)
    return fig_html(fig, 360)


def chart_distribution(ev: pd.DataFrame) -> str:
    ev = rounded(ev)
    fig = go.Figure()
    for col, name, c in (("tm1_rel", "day before (t-1)", C_TM1), ("t0_rel", "print day (t0)", C_T0), ("tp1_rel", "day after (t+1)", C_TP1)):
        fig.add_trace(go.Box(y=ev[col], name=name, marker_color=c, line=dict(width=1.2), boxpoints="all", jitter=0.35, pointpos=0,
                             marker=dict(size=4, opacity=0.55), hovertemplate="%{y:+,.0f} bps<extra>" + name + "</extra>"))
    fig.update_layout(title=dict(text="Spread of moves vs peers, by day (each dot = one print; box = middle half, line = median)", font=dict(size=14)),
                      yaxis_title="bps vs peers", showlegend=False)
    return fig_html(fig, 360)


def chart_follow_through(ev: pd.DataFrame) -> str:
    d = rounded(ev).dropna(subset=["tm1_rel", "t0_rel"])
    strong = d.tm1_rel > compute.FOLLOW_THROUGH_BPS
    fig = go.Figure()
    fig.add_shape(type="rect", x0=compute.FOLLOW_THROUGH_BPS, x1=max(d.tm1_rel.max() * 1.1, 150), y0=0, y1=max(d.t0_rel.max() * 1.1, 100),
                  fillcolor="rgba(42,120,214,0.06)", line=dict(width=0))
    fig.add_trace(go.Scatter(x=d.tm1_rel[~strong], y=d.t0_rel[~strong], mode="markers", name="other prints", text=d.fq_label[~strong],
                             marker=dict(color=GRAY, size=7, opacity=0.7), hovertemplate="%{text}<br>t-1 %{x:+,.0f} / t0 %{y:+,.0f} bps<extra></extra>"))
    n, k = int(strong.sum()), int((d.t0_rel[strong] > 0).sum())
    fig.add_trace(go.Scatter(x=d.tm1_rel[strong], y=d.t0_rel[strong], mode="markers", name=f"t-1 > +100 bps ({k}/{n} up on t0)", text=d.fq_label[strong],
                             marker=dict(color=C_T0, size=9), hovertemplate="%{text}<br>t-1 %{x:+,.0f} / t0 %{y:+,.0f} bps<extra></extra>"))
    fig.add_vline(x=0, line=dict(color="#c9c7c1", width=1)); fig.add_hline(y=0, line=dict(color="#c9c7c1", width=1))
    fig.update_layout(title=dict(text="Did strength into the print carry through? day-before vs print-day move (bps vs peers; shaded = rallied >100 bps into the print)", font=dict(size=14)),
                      xaxis=dict(title="day before the print (bps vs peers)", showgrid=True, gridcolor=GRID, hoverformat=",.0f"), yaxis_title="print day (bps vs peers)")
    return fig_html(fig, 380)


def chart_eps(eps: pd.DataFrame) -> str:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=eps.fq_label, y=eps.consensus, name="pre-print consensus", marker_color="#b9c7d9",
                         hovertemplate="%{x}<br>consensus $%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Bar(x=eps.fq_label, y=eps.eps_actual, name="reported", marker_color=C_T0,
                         customdata=eps.eps_surprise_pct, hovertemplate="%{x}<br>reported $%{y:.2f} (surprise %{customdata:.1f}%)<extra></extra>"))
    fig.update_layout(title=dict(text="EPS: reported vs the consensus the day before the print", font=dict(size=14)), barmode="group", bargap=0.3,
                      yaxis=dict(tickprefix="$", tickformat=".2f", hoverformat=".2f"), xaxis=QAXIS)
    return fig_html(fig, 320)


def chart_cross_section(summary: pd.DataFrame) -> str:
    s = summary.sort_values("avg_abs_rel_t0", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(y=s.ticker, x=s.avg_abs_rel_tm1.round(0), orientation="h", name="day before (t-1)", marker_color=C_TM1, hovertemplate="%{y} day before: %{x:,.0f} bps<extra></extra>"))
    fig.add_trace(go.Bar(y=s.ticker, x=s.avg_abs_rel_t0.round(0), orientation="h", name="print day (t0)", marker_color=C_T0, hovertemplate="%{y} print day: %{x:,.0f} bps<extra></extra>"))
    fig.add_trace(go.Bar(y=s.ticker, x=s.avg_abs_rel_tp1.round(0), orientation="h", name="day after (t+1)", marker_color=C_TP1, hovertemplate="%{y} day after: %{x:,.0f} bps<extra></extra>"))
    fig.update_layout(title=dict(text="Average size of move vs peers, by name (bps): day before / print day / day after", font=dict(size=14)), barmode="group", bargap=0.25,
                      xaxis=dict(showgrid=True, gridcolor=GRID, tickformat=",.0f"), yaxis=dict(showgrid=False))
    return fig_html(fig, 420)


def chart_outperform(summary: pd.DataFrame) -> str:
    s = summary.sort_values("outperform_pct", ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.outperform_pct * 100, y=s.ticker, mode="markers", name="outperform share",
                             error_x=dict(type="data", symmetric=False, array=(s.ci_hi - s.outperform_pct) * 100, arrayminus=(s.outperform_pct - s.ci_lo) * 100,
                                          color="#b5b3ac", thickness=1.2, width=0),
                             marker=dict(color=C_T0, size=9), customdata=np.stack([s.outperform_pct * s.n_events, s.n_events], axis=1),
                             hovertemplate="%{y}: %{x:.0f}% (%{customdata[0]:.0f} of %{customdata[1]:.0f} prints)<extra></extra>"))
    fig.add_vline(x=50, line=dict(color="#c9c7c1", width=1, dash="dot"))
    fig.update_layout(title=dict(text="Share of prints where the stock beat its peers on the print day (dot = share, line = 90% confidence band)", font=dict(size=14)),
                      xaxis=dict(title="% of prints", range=[0, 100], showgrid=True, gridcolor=GRID), yaxis=dict(showgrid=False), showlegend=False)
    return fig_html(fig, 420)


# ----------------------------------------------------------------------------- html
CSS = """
:root{--ink:#0b0b0b;--muted:#52514e;--line:#e6e4df;--surface:#fcfcfb;--card:#ffffff;--accent:#2a78d6;--pos:#2a78d6;--neg:#e34948;--tile:#f4f3ef}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--surface);color:var(--ink);font:15px/1.5 Inter,"Segoe UI",system-ui,-apple-system,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:28px 24px 64px}
header.top{border-bottom:1px solid var(--line);background:#fff}
header.top .wrap{display:flex;gap:18px;align-items:baseline;justify-content:space-between;padding:14px 24px;flex-wrap:wrap}
header.top .brand{font-weight:600;letter-spacing:.2px}header.top nav a{margin-left:14px;font-size:13px;color:var(--muted)}
h1{font-size:26px;margin:6px 0 6px;letter-spacing:-.2px}h2{font-size:18px;margin:34px 0 12px}h3{font-size:14px;margin:0 0 4px;color:var(--muted);font-weight:600}
.sub{color:var(--muted);max-width:900px;margin:0 0 10px}
.method{font-size:12.5px;color:var(--muted);border-left:3px solid var(--line);padding:4px 12px;margin:14px 0 22px;max-width:980px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .k{font-size:12px;color:var(--muted)}.tile .v{font-size:21px;font-weight:600;letter-spacing:-.3px;margin-top:2px;white-space:nowrap}.tile .s{font-size:12px;color:var(--muted)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 12px 4px}
.card h3.ct{font-size:13.5px;font-weight:600;color:var(--ink);margin:0 0 2px 4px}
.obs{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 18px;margin:10px 0 6px}.obs li{margin:8px 0}
table.sum{width:100%;border-collapse:collapse;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
table.sum th,table.sum td{padding:7px 7px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
table.sum th{background:var(--tile);font-weight:600;color:var(--muted);cursor:pointer;position:sticky;top:0}
table.sum tr.grp th{cursor:default;font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#7a7872;text-align:center;border-bottom:none;padding-bottom:0}
table.sum th:first-child,table.sum td:first-child{text-align:left}table.sum tr:hover td{background:#f8f7f4}
table.sum td.pos{color:var(--pos)}table.sum td.neg{color:var(--neg)}
.tablewrap{overflow-x:auto}
.pill{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;background:var(--tile);color:var(--muted);margin-left:6px}
footer{margin-top:40px;color:var(--muted);font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
.pager{display:flex;justify-content:space-between;font-size:13px;margin:6px 0 0}
.events td{font-variant-numeric:tabular-nums}
"""
SORT_JS = """
document.querySelectorAll('table.sortable thead tr:not(.grp) th').forEach((th,i)=>{th.addEventListener('click',()=>{
 const tb=th.closest('table').tBodies[0];const rows=[...tb.rows];const num=v=>parseFloat(v.replace(/[%,()+]/g,'').replace(/^\\((.*)\\)$/,'-$1'));
 const asc=!(th.dataset.asc==='1');th.dataset.asc=asc?'1':'0';
 rows.sort((a,b)=>{const x=a.cells[i].dataset.v??a.cells[i].innerText,y=b.cells[i].dataset.v??b.cells[i].innerText;
  const nx=parseFloat(x),ny=parseFloat(y);const c=(isNaN(nx)||isNaN(ny))?x.localeCompare(y):nx-ny;return asc?c:-c;});
 rows.forEach(r=>tb.appendChild(r));});});
"""
PLOTLY_CDN = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>'


def page(title: str, body: str, nav_links: list[tuple[str, str]], asof: str) -> str:
    nav = "".join(f'<a href="{h}">{html.escape(t)}</a>' for t, h in nav_links)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>{PLOTLY_CDN}</head><body>
<header class="top"><div class="wrap"><span class="brand"><a href="index.html" style="color:inherit">Earnings print reactions</a> <span class="pill">energy · 1Q16 → latest</span></span><nav>{nav}</nav></div></header>
<main class="wrap">{body}</main>
<footer class="wrap">Data: S&amp;P Capital IQ (daily closes, announce dates, EPS actual &amp; pre-print consensus); release timing (BMO/AMC) from company timestamps. Built {asof}. Historical description only — not a recommendation.</footer>
<script>{SORT_JS}</script></body></html>"""


def tile(k, v, s=""):
    return f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>'


def build_site(out: Path = DOCS):
    out.mkdir(parents=True, exist_ok=True)
    uni = pd.read_csv(UNIVERSE_CSV).set_index("ticker")
    overrides = pd.read_csv(TIMING_OVERRIDES_CSV) if TIMING_OVERRIDES_CSV.exists() else pd.DataFrame()
    con = db.connect(DB_PATH)
    asof = dt.date.today().isoformat()
    results, pages = {}, {}
    tickers = list(uni.index)
    for t in tickers:
        row = uni.loc[t]
        bench = row["benchmark"]
        chain = [bench] + [b for b in BENCH_FALLBACKS if b != bench]
        peers = [x for x in uni.index[uni.peer_group == row.peer_group]]
        prices = db.read_prices(con, [t] + chain + peers)
        earn = db.read_earnings(con, t)
        if earn.empty:
            continue
        basket = compute.peer_basket_returns(prices, peers, exclude=t)
        rb = basket.combine_first(compute.benchmark_returns(prices, chain))
        bench_label = f"{row.peer_group} peers"
        ev = compute.event_returns(prices, earn, t, rb, overrides)
        ev_etf = compute.event_returns(prices, earn, t, chain, overrides)
        ps = compute.panel_stats(ev)
        h = compute.header_stats_hist(ev, earn, ps)
        h["first_label"] = ev.fq_label.iloc[0]
        eps = compute.eps_history(earn)
        results[t] = (ev, ps, {"header": h, "ps_etf": compute.panel_stats(ev_etf), "bench_label": bench_label, "peers": [p for p in peers if p != t]})
        pages[t] = dict(ev=ev, ps=ps, h=h, eps=eps, bench_label=bench_label, peers=[p for p in peers if p != t], bench=bench, row=row)
    summary = summarise(results)
    summary.to_csv(out / "summary.csv", index=False)
    bullets = observations(summary, results, uni)
    nav = [("Overview", "index.html")] + [(t, f"{t}.html") for t in tickers]

    # ---------- index
    n_tot = int(summary.n_events.sum())
    tiles = "".join([
        tile("Names", f"{len(summary)}", "large-cap US energy"),
        tile("Prints", f"{n_tot}", f"since {pages[tickers[0]]['h']['first_label']}"),
        tile("Avg size of move vs peers — print day", f"{summary.avg_abs_rel_t0.mean():,.0f} bps", "up or down, averaged across names"),
        tile("…the day before", f"{summary.avg_abs_rel_tm1.mean():,.0f} bps", "positioning day"),
        tile("…the day after", f"{summary.avg_abs_rel_tp1.mean():,.0f} bps", "follow-through day"),
        tile("Beat EPS consensus", f"{summary.beat_rate.mean():.0%}", "of prints, average across names"),
    ])
    rows = []
    for r in summary.sort_values("ticker").itertuples():
        cls = lambda v: "pos" if v > 0 else ("neg" if v < 0 else "")
        ft = f"{r.follow_through_hit:.0%} <span class='pill'>n={r.follow_through_n}</span>" if not pd.isna(r.follow_through_hit) else f"<span class='pill'>n={r.follow_through_n}</span>"
        rows.append(f"<tr><td><a href='{r.ticker}.html'>{r.ticker}</a> <span class='pill'>{uni.loc[r.ticker,'sector']}</span></td>"
                    f"<td data-v='{r.n_events}'>{r.n_events}</td>"
                    f"<td data-v='{r.outperform_pct:.3f}'>{r.outperform_pct:.0%} <span class='pill'>{r.ci_lo:.0%}–{r.ci_hi:.0%}</span></td>"
                    f"<td data-v='{r.outperform_pct_etf:.3f}'>{r.outperform_pct_etf:.0%}</td>"
                    f"<td class='{cls(r.avg_rel_tm1)}' data-v='{r.avg_rel_tm1:.1f}'>{bps(r.avg_rel_tm1)}</td>"
                    f"<td class='{cls(r.avg_rel_t0)}' data-v='{r.avg_rel_t0:.1f}'>{bps(r.avg_rel_t0)}</td>"
                    f"<td class='{cls(r.avg_rel_tp1)}' data-v='{r.avg_rel_tp1:.1f}'>{bps(r.avg_rel_tp1)}</td>"
                    f"<td data-v='{r.avg_abs_rel_t0:.1f}'>{r.avg_abs_rel_t0:,.0f}</td>"
                    f"<td data-v='{r.avg_abs_t0_abs:.1f}'>{r.avg_abs_t0_abs:,.0f}</td>"
                    f"<td data-v='{r.beat_rate:.3f}'>{r.beat_rate:.0%}</td>"
                    f"<td data-v='{(r.follow_through_hit if not pd.isna(r.follow_through_hit) else -1):.3f}'>{ft}</td></tr>")
    H = lambda label, tip: f'<th title="{html.escape(tip)}">{label}</th>'
    table = f"""<div class="tablewrap"><table class="sum sortable"><thead>
<tr class="grp"><th></th><th></th><th colspan="2">Beat on the print day</th><th colspan="3">Average move vs peers (bps)</th><th colspan="2">Avg size of print-day move (bps)</th><th></th><th></th></tr>
<tr>{H("Name", "Ticker · sector")}{H("Prints", "Quarterly prints in the sample since 1Q16")}
{H("vs peers", "Share of prints where the stock beat its equal-weight peer basket on the print day; grey pill = 90% confidence band")}
{H("vs ETF", "Share of prints where the stock beat its sector ETF (OIH for oil services, XLE for producers) on the print day")}
{H("day before", "Average move vs peers on the trading day before the print (t-1)")}
{H("print day", "Average move vs peers on the print day (t0)")}
{H("day after", "Average move vs peers on the trading day after the print (t+1)")}
{H("vs peers", "Average size of the print-day move vs peers, ignoring direction")}
{H("stock itself", "Average size of the stock's own print-day move, ignoring direction")}
{H("Beat EPS consensus", "Share of prints where reported EPS exceeded the consensus that stood the day before")}
{H("Rallied in → up on print", "Of the prints where the stock beat peers by more than 100 bps the day before, the share where it also beat peers on the print day (n = how many such prints)")}
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="sub" style="font-size:12.5px;margin-top:6px"><b>How to read:</b> "vs peers" = the stock's close-to-close move minus the equal-weight average of its peer group that day (OFS: HAL/SLB/BKR · Producers: XOM/CVX/COP/EOG/OXY/FANG), in basis points (100 bps = 1%). "Print day" = the first full session after the release. Hover a column header for its definition; click to sort. Confidence bands are Wilson 90%.</p>"""
    obs = "<ul>" + "".join(f"<li>{html.escape(b)}</li>" for b in bullets) + "</ul>"
    body = f"""
<h1>How liquid US energy names trade around earnings</h1>
<p class="sub">Every quarterly print since 1Q16 for nine large, liquid energy names: how the stock moved versus its peers the day before the print, on the print day and the day after — and how reported EPS compared with the consensus that stood the day before. All moves are in basis points (100 bps = 1%).</p>
<div class="method"><b>Method.</b> Relative = stock minus equal-weight peer basket ex-self (OFS: HAL/SLB/BKR · Producers: XOM/CVX/COP/EOG/OXY/FANG), close-to-close, in basis points. t0 = first full session reflecting the release (BMO → announce day; AMC → next session). Consensus = mean EPS estimate the day before the print. Source: S&amp;P Capital IQ.</div>
<div class="tiles">{tiles}</div>
<h2>What the numbers say</h2><div class="obs">{obs}</div>
<h2>Across the group</h2>
<div class="grid2"><div class="card">{chart_cross_section(summary)}</div><div class="card">{chart_outperform(summary)}</div></div>
<h2>Summary by name</h2>{table}
<p class="sub" style="margin-top:16px">Per-name pages: {" · ".join(f"<a href='{t}.html'>{t}</a>" for t in tickers)}. Data: <a href="summary.csv">summary.csv</a></p>
"""
    (out / "index.html").write_text(page("Earnings print reactions — energy", body, nav, asof), encoding="utf-8")

    # ---------- ticker pages
    for i, t in enumerate(tickers):
        if t not in pages:
            continue
        P = pages[t]; ev, ps, h, eps = P["ev"], P["ps"], P["h"], P["eps"]
        prev_t, next_t = tickers[i - 1] if i > 0 else None, tickers[i + 1] if i < len(tickers) - 1 else None
        big = ev.loc[ev.t0_rel.abs().idxmax()] if ev.t0_rel.notna().any() else None
        tiles = "".join([
            tile("Prints", f"{h['n_prints']}", f"{h['first_label']} → {ev.fq_label.iloc[-1]}"),
            tile(f"Beat {P['bench_label']} on the print day", f"{h['outperform']}/{h['n_prints']} · {h['outperform'] / max(h['n_prints'], 1):.0%}", f"90% confidence band {h['outperform_ci'][0]:.0%}–{h['outperform_ci'][1]:.0%}"),
            tile("Avg size of print-day move", f"{h['avg_abs_t0_rel']:,.0f} bps vs peers", f"{h['avg_abs_t0_abs']:,.0f} bps the stock itself"),
            tile("Beat / miss consensus", f"{h['beats']} / {h['misses']}", f"pre-print mean · {h['inline']} in line · n={h['n_eps']}"),
            tile(f"Last print · {h['last_q_label']}", f"{pct(h['last_t0_abs'], 1)} abs", f"{pct(h['last_t0_rel'], 1)} rel · surprise {pct(h['last_surprise'], 1)} · {h['last_date']}"),
            tile("Biggest print-day move vs peers", f"{bps(big.t0_rel)} bps" if big is not None else "n/a", f"{big.fq_label} · {pd.Timestamp(big.t0).date()}" if big is not None else ""),
        ])
        ev_rows = []
        em = earn_map = db.read_earnings(con, t).set_index("fq_label")
        for r in ev.iloc[::-1].itertuples():
            e = em.loc[r.fq_label] if r.fq_label in em.index else None
            cls = lambda v: "pos" if (v is not None and not pd.isna(v) and v > 0) else ("neg" if (v is not None and not pd.isna(v) and v < 0) else "")
            est = e.eps_est_preprint if e is not None and not pd.isna(e.eps_est_preprint) else (e.eps_est if e is not None else np.nan)
            ev_rows.append(f"<tr><td>{r.fq_label}</td><td>{pd.Timestamp(r.announce_date).date()}</td><td>{r.timing or ''}</td><td>{pd.Timestamp(r.t0).date() if not pd.isna(r.t0) else ''}</td>"
                           f"<td class='{cls(r.tm1_rel)}'>{bps(r.tm1_rel)}</td><td class='{cls(r.t0_rel)}'>{bps(r.t0_rel)}</td><td class='{cls(r.tp1_rel)}'>{bps(r.tp1_rel)}</td>"
                           f"<td class='{cls(r.t0_abs)}'>{bps(r.t0_abs)}</td>"
                           f"<td>{'' if pd.isna(est) else f'{est:.2f}'}</td><td>{'' if e is None or pd.isna(e.eps_actual) else f'{e.eps_actual:.2f}'}</td>"
                           f"<td class='{cls(e.eps_surprise_pct if e is not None else None)}'>{'' if e is None or pd.isna(e.eps_surprise_pct) else f'{e.eps_surprise_pct:+.1f}%'}</td></tr>")
        ev_table = f"""<div class="tablewrap"><table class="sum sortable events"><thead><tr class="grp"><th></th><th></th><th></th><th></th><th colspan="3">Move vs peers (bps)</th><th>Stock (bps)</th><th colspan="3">EPS ($)</th></tr>
<tr><th title="Fiscal quarter reported">Quarter</th><th title="Release date">Announced</th><th title="BMO = before the open, AMC = after the close">Timing</th><th title="First full session reflecting the release">Print day</th><th title="Day before the print, vs peers">day before</th><th title="Print day, vs peers">print day</th><th title="Day after the print, vs peers">day after</th><th title="The stock's own print-day move">print day</th><th title="Consensus the day before the print">consensus</th><th>reported</th><th title="(reported - consensus) / |consensus|">surprise</th></tr></thead><tbody>{''.join(ev_rows)}</tbody></table></div>"""
        ev.to_csv(out / f"{t}_events.csv", index=False)
        pager = f"<div class='pager'><span>{'← ' + f'<a href=\"{prev_t}.html\">{prev_t}</a>' if prev_t else ''}</span><span>{f'<a href=\"{next_t}.html\">{next_t}</a> →' if next_t else ''}</span></div>"
        body = f"""
<p class="sub" style="margin:0"><a href="index.html">← Overview</a></p>
<h1>{t} <span class="pill">{html.escape(str(P['row']['notes']))}</span> <span class="pill">{P['row']['sector']}</span> <span class="pill">peers: {', '.join(P['peers'])}</span> <span class="pill">ETF: {P['bench']}</span></h1>
<p class="sub">"vs peers" = {t}'s close-to-close move minus the equal-weight average of {', '.join(P['peers'])} that day, in basis points (100 bps = 1%). Hover any point for the quarter.</p>
<div class="tiles">{tiles}</div>
<div class="grid2">
<div class="card">{chart_around(ev, P['bench_label'])}</div>
<div class="card">{chart_t0(ev, P['bench_label'])}</div>
<div class="card">{chart_distribution(ev)}</div>
<div class="card">{chart_follow_through(ev)}</div>
</div>
<div class="card" style="margin-top:16px">{chart_eps(eps)}</div>
<h2>Every print <span class="pill">newest first · <a href="{t}_events.csv">csv</a></span></h2>{ev_table}
{pager}
"""
        (out / f"{t}.html").write_text(page(f"{t} — earnings print reactions", body, nav, asof), encoding="utf-8")
    (out / ".nojekyll").write_text("")
    print(f"site -> {out}  ({len(pages)} ticker pages + index)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DOCS))
    a = ap.parse_args()
    build_site(Path(a.out))
