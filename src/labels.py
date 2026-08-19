"""Fiscal-quarter label helpers: everything is normalised to '1Q23' style."""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

_CIQ = re.compile(r"^(?P<y>\d{4})\s*FQ\s*(?P<q>[1-4])$")          # 2025FQ2  (item 336831)
_CIQ2 = re.compile(r"^FQ(?P<q>[1-4])(?P<y>\d{4})$")                # FQ22025 (period arg form)
_VA = re.compile(r"^(?P<q>[1-4])QFY-?(?P<y>\d{4})$")               # 1QFY-2023 (Visible Alpha)
_SHORT = re.compile(r"^(?P<q>[1-4])Q(?P<y>\d{2,4})$")              # 1Q23 / 1Q2023


def normalise(label) -> str | None:
    if label is None or (isinstance(label, float) and pd.isna(label)):
        return None
    s = str(label).strip().upper().replace(" ", "")
    for rx in (_CIQ, _CIQ2, _VA, _SHORT):
        m = rx.match(s)
        if m:
            y = int(m.group("y"))
            y = y if y > 100 else 2000 + y
            return f"{int(m.group('q'))}Q{y % 100:02d}"
    return None


def from_period_end(period_end, fye_month: int = 12) -> str | None:
    """Derive fiscal-quarter label from a period-end date and fiscal-year-end month.
    Fiscal year is named by the calendar year in which it ends."""
    if period_end is None or pd.isna(period_end):
        return None
    d = pd.Timestamp(period_end)
    months_into_fy = (d.month - fye_month - 1) % 12          # 0..11
    q = months_into_fy // 3 + 1
    fy = d.year if d.month <= fye_month else d.year + 1
    return f"{q}Q{fy % 100:02d}"


def sort_key(label: str) -> tuple:
    q, y = label.split("Q")
    return (int(y), int(q))


def label_from_date_guess(announce: date, fye_month: int = 12) -> str:
    """Rough guess of the quarter being reported from the announce date (report ~1-2 months after period end)."""
    d = pd.Timestamp(announce) - pd.DateOffset(days=5)
    pe = d - pd.offsets.QuarterEnd(1)     # last calendar quarter end strictly before d
    return from_period_end(pe, fye_month)
