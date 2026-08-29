# Earnings Reaction Tracker

Rebuild of the Isentropic Capital per-ticker "earnings tracker": how a stock trades **relative to its
peer ETF** on the day before (t-1), the day of (t0) and the day after (t+1) each of the last 20 prints,
plus header stats and EPS reported-vs-consensus history. History window: **1Q16 -> last reported quarter** (42 quarters as of Aug-2026; `--start-fq` in the workbook generator). Output = one PNG per ticker + `output/index.html`.

## Quick start

```bash
cd "VIC/Tool/Earnings Trackers/tracker"
python -m pytest -q tests                # unit tests for the event-window math
python -m src.build --all --demo         # synthetic data -> output/demo/  (layout preview)
python -m src.ingest_yahoo --all         # bootstrap real data (Yahoo) -> data/db.sqlite
python -m src.build --all                # dashboards -> output/*.png, index.html, summary.csv
python -m src.build --all --pm           # PM draft PNG/PDF pack -> output/pm_draft/
python -m src.site                       # GitHub-Pages site -> docs/ (index.html + one page per ticker, interactive Plotly charts)
```

## Publishing the site (GitHub Pages)
`docs/` is a self-contained static site (Plotly from CDN, Inter from Google Fonts; `.nojekyll` included).
1. `git init` in `tracker/` (a `.gitignore` keeps the DB, workbooks and output out), commit, push to a repo.
2. Repo → Settings → Pages → Source: *Deploy from a branch*, branch `main`, folder **/docs**.
3. Share `https://<user>.github.io/<repo>/` — the overview page; each name is `<TICKER>.html`. Re-run `python -m src.site` and push to update.
Preview locally: `python -m http.server 8765 --directory docs` (or the `docs-site` entry in `.claude/launch.json`).

Site features: per-name takeaways (generated from the numbers), print-day move by condition (beat/miss consensus; rallied/flat/sold-off into the print),
peers-vs-ETF and all-vs-last-20-quarters toggles, bundled Plotly (works offline / behind CDN blocks), data-as-of + next expected print dates.
Site design: light theme, Inter, KPI tiles, sortable summary table, one fixed colour role per window (t0 blue, t-1 orange, t+1 aqua;
blue/red only where sign is the message), hover tooltips on every mark, n and 90% Wilson bands on every hit rate, method strip on every page.

## Data sources (in priority order)

| Source | What | Script |
|---|---|---|
| **CapIQ Excel plugin** (primary) | 5y daily closes (`SPGRANGEV`), 20 quarters of announce date / EPS est / actual / surprise % / #est, pre-print consensus, FQ+1 revision (`SPGTable`) | `build_pull_workbook.py` → `ciq_pull_v3.xlsx` → refresh in Excel → `ingest_ciq_xlsx.py` |
| **Yahoo (yfinance)** (bootstrap / cross-check) | adjusted closes, earnings dates **with time-of-day → BMO/AMC**, EPS est/actual/surprise | `ingest_yahoo.py` |
| Visible Alpha add-in (cross-check) | `VADetail("EarningsDate")`, PreQ consensus | not wired yet (see plan) |
| Manual | options-implied move | `config/universe.csv` column `implied_move` |

Rows carry a `source` column; the CapIQ ingest overwrites Yahoo rows for the same (ticker, quarter).

### CapIQ workflow (v3 workbook = 1Q16 start)
1. `python -m src.build_pull_workbook [--start-fq 1Q16]` -> `ciq_pull_v3.xlsx` (42 quarters x 7 mnemonics = 300-row SPGTable blocks; prices from 2015-11-02) (default periods are **absolute** `FQ32021...`, derived
   per ticker from an anchor cell `=SPG(tkr,"336831","FQ0",Sdate)` -> `"2026FQ2"`; `--period-mode relative` is optional).
2. Open `ciq_pull_v3.xlsx` in Excel with the S&P Capital IQ Pro add-in loaded; refresh twice (`EPS_PrePrint`
   depends on the announce dates in `Earnings`); save.
   Sheets: `Universe` (dates) - `Prices` (1 `SPGRANGEV` per instrument, row 7) - `Earnings` (per ticker one
   `SPGTable` in row 8/key column; columns key | mnemonic | period | as_of | **value**; ticker cell in row 9 of the
   value column; results land in the value column rows 10-155) - `EPS_PrePrint` (consensus as-of announce-1) -
   `Fallback_Scalar` (one `SPG()` per row, `--use-fallback`) - `Test` (one cell per candidate call: instant feedback).
3. `python -m src.ingest_ciq_xlsx` - prints rows/unresolved (`#PEND`, `NA`, `#INVALID...`) per block.
4. `python -m src.build --all`.

**Refresh-1 findings (2026-08-18, reviewed live in Excel):** plugin works; `SPGRANGEV` prices spilled ~1,280 rows;
`SP_PERIOD_END`, `SP_EPS_EST`, `SP_EST_ACT_EPS`, `336831` resolve with relative periods; but
`SP_EARNINGS_ANNOUNCE_DATE` with a relative period (`FQ-19`) returned `#INVALID FUNCTION PARAMETER`. The vendor
templates only ever call it with absolute periods (`FQ32023`, `FY2021`) - hence v2 uses absolute periods.
`Test!B5:B10` isolate the announce-date call (absolute / no as-of / relative / FQ0 / FQ+1); if the absolute form
also fails, the mnemonic name itself must be confirmed in the CIQ formula builder and swapped in `Q_MNEMONICS`.
v1 bugs fixed in v2: block columns were shifted by one (key label written into the mnemonic column), so `SPGTable`
received key/mnemonic/period instead of mnemonic/period/as-of and the value column collided with as-of;
`EPS_PrePrint` referenced the wrong columns. The v1 file is kept as `data/raw/ciq_pull_v1_unrefreshed_backup.xlsx`.

**Refresh-2 (v2, 2026-08-18) - confirmed working:** `Test` sheet shows `SP_EARNINGS_ANNOUNCE_DATE` resolves with
absolute periods (`FQ32021` -> 10/19/2021) and fails only with relative ones; `SPGTable` long-form filled all 9
blocks (20/20 quarters, 0 unresolved), `EPS_PrePrint` returned per-quarter pre-print consensus, `SPGRANGEV` spilled
1,276 closes per stock. All 180 CapIQ announce dates match Yahoo exactly. Open: ETF tickers `NYSEARCA:XLE/OIH`
returned `#INVALID COMPANY ID` -> refresh `ciq_etf_ticker_test.xlsx`, put the working format in `config/benchmarks.csv`,
regenerate. Until then the benchmark series stay Yahoo-sourced (fine: no ETF ex-div dates fall in the event windows).
The CapIQ ingest keeps Yahoo's per-event BMO/AMC timing and next-call date when CapIQ has none.

**Refresh-3 (v3 = 1Q16 window, 2026-08-19) - ingested:** 9 stocks x 2,713 closes (BKR 2,293 from its Jul-2017 listing),
42 quarters each (BKR 37 valid). Reconciliation vs Yahoo: 370/374 announce dates identical, the 4 diffs favour CapIQ
(HAL 1Q16 = 4/22 press release vs 5/3 delayed call; OXY 1Q19 = Sunday 5/5 release); EPS actuals identical to the cent;
`SP_PRICE_CLOSE` = Yahoo unadjusted close (split-adjusted, not dividend-adjusted). Pre-print consensus differs from
final consensus in 11% of quarters. CapIQ ingest is now authoritative per ticker (deletes other-source rows so
adjusted/unadjusted series never mix). ETF tickers: CIQ accepts `ARCA:XLE` / `XLE` / `XLE-US` (not `NYSEARCA:`);
`benchmarks.csv` updated; `python -m src.build_pull_workbook --etf-only` -> `ciq_pull_etf.xlsx` (3 cells) ->
refresh -> `python -m src.ingest_ciq_xlsx --xlsx ciq_pull_etf.xlsx --layout data/ciq_pull_etf_layout.json`.
The generator now backs up any existing workbook to `data/raw/` before overwriting.

## Definitions
* **t0** = first full session reflecting the print (BMO → announce day; AMC → next trading day). Timing comes
  from Yahoo timestamps or `config/universe.csv:timing_default`, overridable per event in `config/timing_overrides.csv`.
  Events where t0 was quiet (<1%) but t+1 moved >4% are flagged `[timing check]` in the build log.
* **Absolute bps** = stock close-to-close return × 10,000; **Relative bps** = stock − benchmark. Default build: benchmark
  ETF (OIH for OFS, XLE for producers; gap fallback → XLE → SPY). `--pm` build: equal-weight **peer basket ex-self**
  (`peer_group` in universe.csv: OFS = HAL/SLB/BKR; Producers = XOM/CVX/COP/EOG/OXY/FANG), ETF chain as gap fallback.
* Hit rates carry a Wilson 90% band (`compute.wilson`); header/cover show n everywhere.
* Pie = share of quarters with relative t0 > 0. Range = max/min/mean relative per window. Follow-through = quarters
  with relative t-1 > +100 bps. 4wk EPS change = FQ+1 consensus now ÷ 28 days ago − 1 (CapIQ only).

## Layout
```
config/universe.csv          ticker, ciq_ticker, va_ticker, benchmark, timing_default, sector, fye_month, implied_move
config/benchmarks.csv        ETF list (XLE, OIH, SPY)
config/timing_overrides.csv  ticker, fq, timing
ciq_pull_v3.xlsx             generated CapIQ pull workbook (v2 = 20-quarter version, refreshed 2026-08-18)   (data/ciq_pull_layout.json = cell map for ingest)
data/db.sqlite               prices / earnings / forward / ingest_log
src/  build_pull_workbook.py ingest_ciq_xlsx.py ingest_yahoo.py compute.py render.py build.py db.py labels.py demo_data.py
tests/test_compute.py
output/<TICKER>.png, <TICKER>_events.csv, summary.csv, cross_section.png, index.html
```

## Validation done
1Q16-1Q23 overlap with the tweet screenshots (Yahoo-sourced, 42-quarter run): HAL absolute t0 moves match the
original's dots quarter by quarter (−383, +425, −292, −421, +640, −810, +915, +640, −358, +367, −354 bps), HAL 2Q18
relative −582 vs ≈−580 shown, and "missed consensus in 2 of 42 quarters" vs the tweet's "twice in 44".
Yahoo-sourced HAL/BKR reproduce the Jul-2023 tweet screenshots on overlapping quarters:
HAL 1Q23 EPS-day −354 bps (tweet: −3.5%), BKR 1Q23 +359 bps (tweet: +3.6%), BKR 2Q22 ≈ −826 / 3Q22 ≈ +608 bps
(tweet ≈ −750 / +600). BKR's switch to after-close reporting in Oct-2023 is picked up from timestamps.

## Earnings pack for any name (skill)
`src/cruise.py` is group-parameterized: `python -m src.cruise {workbook|yahoo|ingest|pack|charts} --group <g> [--start 1Q23] [--focus T]`
with `config/<g>_universe.csv` (ticker, ciq_ticker, timing_default, fye_month, notes, role focus|peer). Yahoo bootstraps prices/EPS/dates;
the generated `ciq_pull_<g>.xlsx` adds Rev/EBITDA consensus after one Excel refresh. Outputs in `output/<g>/`: Excel pack, per-ticker price+prints
PNGs, beats panels, indexed comparison, focus-vs-peers print-day panel. Invoke via the personal skill `/earnings-pack` (~/.claude/skills/earnings-pack).

## Next (plan Phase 3)
Universe screen via `SP_CONSTITUENTS` + liquidity filters; implied move from IBKR options; Excel renderer with
native charts + ticker dropdown; dispersion / rating-change / revenue-surprise overlays; sector heat-map for the
current earnings week.
