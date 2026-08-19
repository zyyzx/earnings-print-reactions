"""Render the per-ticker earnings dashboard (PNG) mirroring the Isentropic layout.

Panels: header boxes | outperform pie
        relative range (t-1/t0/t+1) | abs & rel summary by quarter
        follow-through after >100bps t-1 | relative t-1/t0/t+1 by quarter
        EPS reported vs consensus (bonus)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import FuncFormatter
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from .compute import PanelStats, WINDOWS

# fixed palette (kept close to the original Excel look; assigned by role, never cycled)
C_HIGH, C_LOW, C_AVG = "#4472C4", "#A5A5A5", "#FFC000"
C_REL, C_ABS = "#9DC3E6", "#F4B183"
C_OUT, C_UNDER = "#C5E0B4", "#F8CBAD"
C_TM1, C_T0, C_TP1 = "#FFD966", "#00B050", "#262626"
C_ACT, C_CONS = "#4472C4", "#C0504D"
INK, MUTED, GRID = "#333333", "#595959", "#D9D9D9"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 10, "axes.titlecolor": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
})


def _fmt_paren(x, _pos=None):
    if np.isnan(x):
        return ""
    return f"({abs(x):,.0f})" if x < 0 else f"{x:,.0f}"


def _style(ax, title):
    ax.set_title(title, loc="center", pad=8)
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_paren))
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.axhline(0, color=GRID, linewidth=0.8)
    ax.tick_params(axis="both", length=0)


def _pct(x, digits=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{digits}f}%"


def _header(ax, ticker: str, h: dict):
    ax.axis("off")
    boxes = [
        (0.00, "Equity", ticker, True),
        (0.00, "Earnings Call", str(h.get("next_call") or "n/a"), False),
        (0.00, "Earnings Implied Move", _pct(h.get("implied_move")), False),
        (0.52, "4wk EPS change", _pct(h.get("eps_4wk_change")), False),
        (0.52, "EPS surprise Last Q", _pct(h.get("eps_surprise_last_q")), False),
        (0.52, f"{h.get('last_q_label') or 'Last Q'} EPS day performance", _pct(h.get("last_q_eps_day_perf")), False),
    ]
    ys = [0.85, 0.52, 0.19]
    for i, (x, label, val, hi) in enumerate(boxes):
        y = ys[i % 3]
        ax.text(x, y + 0.08, label, fontsize=9, color=INK, transform=ax.transAxes, va="bottom")
        ax.text(x + 0.01, y - 0.05, val, fontsize=9.5, color="#1F4E79" if hi else INK, weight="bold" if hi else "normal",
                transform=ax.transAxes, va="bottom",
                bbox=dict(boxstyle="square,pad=0.35", fc="#FFF2CC" if hi else "white", ec=INK, lw=0.8))


def _header_hist(ax, ticker: str, h: dict, bench_label: str):
    """Historical-only header boxes (PM draft)."""
    ax.axis("off")
    ci = h.get("outperform_ci") or (np.nan, np.nan)
    n = h.get("n_prints") or 0
    op = f"{h['outperform']}/{n} ({h['outperform'] / n:.0%})" if n else "n/a"
    ci_txt = f"90% band {ci[0]:.0%}-{ci[1]:.0%}" if n and not np.isnan(ci[0]) else ""
    last = (f"{h.get('last_q_label')}  {h.get('last_date')}  |  surprise {_pct(h.get('last_surprise'))}  |  "
            f"t0 {_pct(h.get('last_t0_abs'))} abs / {_pct(h.get('last_t0_rel'))} rel") if h.get("last_q_label") else "n/a"
    boxes = [
        (0.00, "Equity", ticker, True),
        (0.00, "Prints in sample", f"{n}  (since {h.get('first_label', '1Q16')})", False),
        (0.00, f"Outperformed {bench_label} on t0", f"{op}   {ci_txt}", False),
        (0.52, "Avg |t0| move (bps)", f"rel {h.get('avg_abs_t0_rel', np.nan):,.0f}   /   abs {h.get('avg_abs_t0_abs', np.nan):,.0f}", False),
        (0.52, "EPS vs pre-print consensus", f"beat {h.get('beats')}  /  miss {h.get('misses')}  /  in line {h.get('inline')}   (n={h.get('n_eps')})", False),
        (0.52, "Last print", last, False),
    ]
    ys = [0.85, 0.52, 0.19]
    for i, (x, label, val, hi) in enumerate(boxes):
        y = ys[i % 3]
        ax.text(x, y + 0.08, label, fontsize=9, color=INK, transform=ax.transAxes, va="bottom")
        ax.text(x + 0.01, y - 0.05, val, fontsize=9.5 if hi else 8.8, color="#1F4E79" if hi else INK,
                weight="bold" if hi else "normal", transform=ax.transAxes, va="bottom",
                bbox=dict(boxstyle="square,pad=0.35", fc="#FFF2CC" if hi else "white", ec=INK, lw=0.8))


def _pie(ax, ticker, ps: PanelStats):
    if ps.n_events == 0:
        ax.text(0.5, 0.5, "no events", ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
        return
    vals = [ps.outperform, ps.underperform]
    wedges, _ = ax.pie(vals, colors=[C_OUT, C_UNDER], startangle=90, counterclock=False,
                       wedgeprops=dict(linewidth=1.5, edgecolor="white"))
    for w, lab, v in zip(wedges, ("Outperform", "Underperform"), vals):
        ang = np.deg2rad((w.theta1 + w.theta2) / 2)
        x, y = 1.25 * np.cos(ang), 1.25 * np.sin(ang)
        ax.text(x, y, f"{lab}\n{v}/{ps.n_events} ({v / ps.n_events:.0%})", ha="center", va="center", fontsize=8.5,
                bbox=dict(boxstyle="square,pad=0.3", fc="white", ec=GRID, lw=0.8))
    ci = getattr(ps, "outperform_ci", (np.nan, np.nan))
    band = f"  (90% band {ci[0]:.0%}-{ci[1]:.0%})" if not np.isnan(ci[0]) else ""
    ax.set_title(f"{ticker} Out/Under Performance on Earnings (relative, t0)" + band)


def _range(ax, ticker, ps: PanelStats):
    x = np.arange(3)
    r = ps.range
    ax.bar(x, r["high"].clip(lower=0), width=0.6, color=C_HIGH, label="High")
    ax.bar(x, r["low"].clip(upper=0), width=0.6, color=C_LOW, label="Low")
    ax.scatter(x, r["avg"], color=C_AVG, edgecolor=INK, linewidth=0.5, s=40, zorder=3, label="AVG")
    for xi, v in zip(x, r["avg"]):
        if not np.isnan(v):
            ax.annotate(_fmt_paren(v), (xi, v), textcoords="offset points", xytext=(14, -3), fontsize=8, color=INK)
    ax.set_xticks(x, ["t-1", "t0", "t+1"])
    _style(ax, f"{ticker} Relative Performance Range (bps) Around Earnings")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.22), ncol=3, frameon=False, fontsize=8)


def _summary(ax, ticker, ev: pd.DataFrame):
    x = np.arange(len(ev))
    ax.bar(x, ev["t0_rel"], width=0.35, color=C_REL, label="Relative")
    ax.scatter(x, ev["t0_abs"], color=C_ABS, s=22, zorder=3, label="Absolute")
    ax.set_xticks(x, ev["fq_label"], rotation=90, fontsize=7 if len(ev) > 28 else 7.5)
    _style(ax, f"{ticker} Absolute & Relative Performance Summary (bps) by Quarter — t0")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), ncol=2, frameon=False, fontsize=8)


def _follow_through(ax, ticker, ev: pd.DataFrame, ps: PanelStats):
    x = np.arange(len(ev))
    mask = ev["fq_label"].isin(ps.follow_through.fq_label)
    rel = ev["t0_rel"].where(mask)
    ab = ev["t0_abs"].where(mask)
    ax.bar(x - 0.18, rel, width=0.36, color=C_LOW, label="Relative")
    ax.bar(x + 0.18, ab, width=0.36, color="#5B9BD5", label="Absolute")
    ax.set_xticks(x, ev["fq_label"], rotation=90, fontsize=7 if len(ev) > 28 else 7.5)
    n = int(mask.sum())
    if n == 0:
        vals = pd.concat([ev["t0_rel"], ev["t0_abs"]]).dropna()
        lim = float(vals.abs().max()) * 1.1 if len(vals) else 500.0
        ax.set_ylim(-lim, lim)
        ax.text(0.5, 0.5, "no quarters with >100 bps relative outperformance on t-1", ha="center", va="center",
                transform=ax.transAxes, fontsize=9, color=MUTED)
    hit = f", t0 outperform {ps.hit_rate_follow_through:.0%}" if ps.hit_rate_follow_through is not None else ""
    _style(ax, f"{ticker} Performance (bps) on t0 After >100bps outperformance on t-1  (n={n}{hit})")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), ncol=2, frameon=False, fontsize=8)


def _around(ax, ticker, ev: pd.DataFrame):
    x = np.arange(len(ev))
    lo = ev[["tm1_rel", "t0_rel", "tp1_rel"]].min(axis=1)
    hi = ev[["tm1_rel", "t0_rel", "tp1_rel"]].max(axis=1)
    ax.vlines(x, lo, hi, color=INK, linewidth=0.8, zorder=2)
    ax.scatter(x, ev["tm1_rel"], marker="o", color=C_TM1, s=22, zorder=3, label="t-1")
    ax.scatter(x, ev["t0_rel"], marker="s", color=C_T0, s=34, zorder=4, label="t0")
    ax.scatter(x, ev["tp1_rel"], marker="^", color=C_TP1, s=22, zorder=3, label="t+1")
    ax.set_xticks(x, ev["fq_label"], rotation=90, fontsize=7 if len(ev) > 28 else 7.5)
    _style(ax, f"{ticker} Relative Performance Around Earnings (bps) by Quarter")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), ncol=3, frameon=False, fontsize=8)


def _eps(ax, ticker, eps: pd.DataFrame):
    if eps.empty:
        ax.axis("off")
        return
    x = np.arange(len(eps))
    ax.bar(x - 0.2, eps["eps_actual"], width=0.4, color=C_ACT, label="Reported")
    ax.bar(x + 0.2, eps["consensus"], width=0.4, color=C_CONS, label="Consensus")
    ax.set_xticks(x, eps["fq_label"], rotation=90, fontsize=7 if len(eps) > 28 else 7.5)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"(${abs(v):.2f})" if v < 0 else f"${v:.2f}"))
    ax.set_title(f"{ticker} EPS Reported vs Consensus")
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0)
    lo, hi = ax.get_ylim()
    ax.set_ylim(min(lo, 0), hi * 1.15 if hi > 0 else hi)
    misses = int(((eps["eps_actual"] - eps["consensus"]) < 0).sum())
    n = int(eps["eps_actual"].notna().sum())
    ax.text(0.99, 0.97, f"missed consensus in {misses} of {n} quarters", transform=ax.transAxes, ha="right", va="top",
            fontsize=8, color=MUTED)
    ax.legend(loc="upper left", ncol=2, frameon=False, fontsize=8)


def render_dashboard(ticker: str, ev: pd.DataFrame, ps: PanelStats, header: dict, eps: pd.DataFrame,
                     benchmark: str, out_path, note: str = "", mode: str = "full", pdf=None):
    """mode='full' = original header (next call / implied move / revision); mode='hist' = historical-only header."""
    n = max(len(ev), 20)
    fig = plt.figure(figsize=(15 + 0.22 * (n - 20), 17), dpi=110, facecolor="white")
    gs = GridSpec(4, 2, figure=fig, height_ratios=[0.9, 1.15, 1.15, 0.9], hspace=0.65, wspace=0.25,
                  left=0.06, right=0.98, top=0.95, bottom=0.05)
    if mode == "hist":
        _header_hist(fig.add_subplot(gs[0, 0]), ticker, header, benchmark)
    else:
        _header(fig.add_subplot(gs[0, 0]), ticker, header)
    _pie(fig.add_subplot(gs[0, 1]), ticker, ps)
    _range(fig.add_subplot(gs[1, 0]), ticker, ps)
    _summary(fig.add_subplot(gs[1, 1]), ticker, ev)
    _follow_through(fig.add_subplot(gs[2, 0]), ticker, ev, ps)
    _around(fig.add_subplot(gs[2, 1]), ticker, ev)
    _eps(fig.add_subplot(gs[3, :]), ticker, eps)
    foot = (f"Method: relative = stock minus {benchmark} close-to-close return (bps). t0 = first full session reflecting the print "
            f"(BMO: announce day; AMC: next session). Prices/estimates: S&P Capital IQ; consensus = pre-print mean. {note}")
    fig.text(0.06, 0.008, foot, fontsize=7.5, color=MUTED)
    fig.savefig(out_path)
    if pdf is not None:
        pdf.savefig(fig)
    plt.close(fig)
    return out_path


def render_cross_section(summary: pd.DataFrame, out_path):
    """Avg |relative| move (bps) t-1 / t0 / t+1 across the universe (the 'positioning squaring' chart)."""
    fig, ax = plt.subplots(figsize=(6, 4), dpi=110, facecolor="white")
    vals = [summary["avg_abs_rel_tm1"].mean(), summary["avg_abs_rel_t0"].mean(), summary["avg_abs_rel_tp1"].mean()]
    ax.bar(np.arange(3), vals, width=0.45, color="#548235")
    for i, v in enumerate(vals):
        ax.annotate(f"{v:,.0f}", (i, v), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8)
    ax.set_xticks(np.arange(3), ["t-1", "t0", "t1"])
    _style(ax, f"Avg |Stock Move| Relative to Benchmark Before, On, After Earnings (bps, n={len(summary)})")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def render_cover(summary: pd.DataFrame, bullets: list[str], out_path, title: str, subtitle: str, pdf=None):
    """Cover page: title, observations, summary table, cross-section bars."""
    import textwrap
    fig = plt.figure(figsize=(15, 10.5), dpi=110, facecolor="white")
    gs = GridSpec(3, 2, figure=fig, height_ratios=[0.42, 0.9, 1.05], width_ratios=[1.75, 1.0], hspace=0.25, wspace=0.22,
                  left=0.05, right=0.97, top=0.96, bottom=0.06)
    ax0 = fig.add_subplot(gs[0, :]); ax0.axis("off")
    ax0.text(0, 0.95, title, fontsize=17, weight="bold", color="#1F4E79", va="top")
    for k, line in enumerate(textwrap.wrap(subtitle, 175)):
        ax0.text(0, 0.45 - 0.22 * k, line, fontsize=9.5, color=MUTED, va="top")
    axb = fig.add_subplot(gs[1, :]); axb.axis("off")
    axb.text(0, 1.02, "Observations (every figure traceable to summary.csv / <TICKER>_events.csv)", fontsize=10, weight="bold", color=INK, va="top")
    y = 0.90
    for b in bullets:
        lines = textwrap.wrap(b, 165)
        for k, line in enumerate(lines):
            axb.text(0.0 if k == 0 else 0.012, y, ("\u2022 " if k == 0 else "") + line, fontsize=9.4, color=INK, va="top")
            y -= 0.085
        y -= 0.04
    axt = fig.add_subplot(gs[2, 0]); axt.axis("off")
    hdr = ["Ticker", "Prints", "Outperf\nt0", "90%\nband", "Avg rel\nt-1", "Avg rel\nt0", "Avg rel\nt+1", "Avg\n|t0 rel|", "Beat\nrate", "t-1 >100bps\n-> t0 up"]
    cells = []
    for r in summary.itertuples():
        ft = f"{r.follow_through_hit:.0%} (n={r.follow_through_n})" if not pd.isna(r.follow_through_hit) else f"n={r.follow_through_n}"
        cells.append([r.ticker, f"{r.n_events}", f"{r.outperform_pct:.0%}", f"{r.ci_lo:.0%}-{r.ci_hi:.0%}",
                      _fmt_paren(r.avg_rel_tm1), _fmt_paren(r.avg_rel_t0), _fmt_paren(r.avg_rel_tp1),
                      f"{r.avg_abs_rel_t0:,.0f}", f"{r.beat_rate:.0%}", ft])
    widths = [0.08, 0.07, 0.09, 0.11, 0.09, 0.09, 0.09, 0.09, 0.08, 0.15]
    tbl = axt.table(cellText=cells, colLabels=hdr, loc="upper center", cellLoc="right", colLoc="right", colWidths=widths)
    tbl.auto_set_font_size(False); tbl.set_fontsize(8.2); tbl.scale(1, 1.3)
    for (i, j), c in tbl.get_celld().items():
        c.set_edgecolor(GRID)
        if i == 0:
            c.set_facecolor("#DDEBF7"); c.set_text_props(weight="bold"); c.set_height(c.get_height() * 1.6)
        if j == 0:
            c.set_text_props(ha="left")
    axt.set_title("Summary by name (bps; relative = vs equal-weight peer basket ex-self)", fontsize=9.5, color=MUTED, loc="left")
    axc = fig.add_subplot(gs[2, 1])
    vals = [summary["avg_abs_rel_tm1"].mean(), summary["avg_abs_rel_t0"].mean(), summary["avg_abs_rel_tp1"].mean()]
    axc.bar(np.arange(3), vals, width=0.45, color="#548235")
    for i, v in enumerate(vals):
        axc.annotate(f"{v:,.0f}", (i, v), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8)
    axc.set_xticks(np.arange(3), ["t-1", "t0", "t+1"])
    _style(axc, f"Avg |move| vs peers before / on / after the print (bps, {len(summary)} names)")
    fig.savefig(out_path)
    if pdf is not None:
        pdf.savefig(fig)
    plt.close(fig)
    return out_path
