"""SQLite storage: prices, earnings (per ticker × fiscal quarter), forward (next quarter)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from .paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL, date TEXT NOT NULL, close REAL,
    source TEXT, PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS earnings (
    ticker TEXT NOT NULL, fq_label TEXT NOT NULL,
    offset INTEGER, announce_date TEXT, period_end TEXT,
    eps_est REAL, eps_est_preprint REAL, eps_actual REAL,
    eps_surprise_pct REAL, eps_num_est REAL,
    timing TEXT, source TEXT, PRIMARY KEY (ticker, fq_label));
CREATE TABLE IF NOT EXISTS forward (
    ticker TEXT PRIMARY KEY, asof TEXT, next_fq_label TEXT, next_announce_date TEXT,
    next_period_end TEXT, next_eps_est_now REAL, next_eps_est_m28d REAL,
    next_eps_num_est REAL, source TEXT);
CREATE TABLE IF NOT EXISTS ingest_log (
    ts TEXT, source TEXT, table_name TEXT, rows INTEGER, missing INTEGER, note TEXT);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def upsert(con: sqlite3.Connection, table: str, df: pd.DataFrame, keys: list[str]) -> int:
    """Insert-or-replace rows of df into table (df columns must exist in table)."""
    if df.empty:
        return 0
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    cols = list(df.columns)
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in rec)
            for rec in df.itertuples(index=False, name=None)]
    con.executemany(sql, rows)
    con.commit()
    return len(rows)


def read_prices(con, tickers: list[str] | None = None) -> pd.DataFrame:
    q = "SELECT ticker, date, close FROM prices"
    if tickers:
        q += " WHERE ticker IN (%s)" % ",".join("?" * len(tickers))
        df = pd.read_sql(q, con, params=tickers)
    else:
        df = pd.read_sql(q, con)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def read_earnings(con, ticker: str) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM earnings WHERE ticker=? ORDER BY announce_date", con, params=[ticker])
    for c in ("announce_date", "period_end"):
        df[c] = pd.to_datetime(df[c])
    return df


def read_forward(con, ticker: str) -> dict:
    df = pd.read_sql("SELECT * FROM forward WHERE ticker=?", con, params=[ticker])
    return {} if df.empty else df.iloc[0].to_dict()


def log(con, source: str, table: str, rows: int, missing: int, note: str = ""):
    con.execute("INSERT INTO ingest_log VALUES (datetime('now'),?,?,?,?,?)", (source, table, rows, missing, note))
    con.commit()
