"""Shared paths for the tracker package."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DATA = ROOT / "data"
RAW = DATA / "raw"
OUTPUT = ROOT / "output"
DB_PATH = DATA / "db.sqlite"
PULL_XLSX = ROOT / "ciq_pull_v3.xlsx"
PULL_LAYOUT = DATA / "ciq_pull_layout.json"
UNIVERSE_CSV = CONFIG / "universe.csv"
BENCHMARKS_CSV = CONFIG / "benchmarks.csv"
TIMING_OVERRIDES_CSV = CONFIG / "timing_overrides.csv"

for _p in (CONFIG, DATA, RAW, OUTPUT):
    _p.mkdir(parents=True, exist_ok=True)
