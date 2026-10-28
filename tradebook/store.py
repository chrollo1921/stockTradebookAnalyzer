"""Local SQLite history of fills, so exports of different date ranges add up over time.

A future auto-fetch job (e.g. Kite Connect) can write into the same store with `add()`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from .parser import merge_tradebooks

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "trades.db"


class TradeStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _conn(self):
        return sqlite3.connect(self.path)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return merge_tradebooks([])
        with self._conn() as c:
            try:
                df = pd.read_sql("SELECT * FROM fills", c)
            except Exception:
                return merge_tradebooks([])
        for col in ["trade_date", "order_execution_time"]:
            df[col] = pd.to_datetime(df[col])
        return df

    def add(self, fills: pd.DataFrame) -> int:
        """Merge new fills into the store. Returns how many were new."""
        before = self.load()
        merged = merge_tradebooks([before, fills]) if len(before) else merge_tradebooks([fills])
        with self._conn() as c:
            merged.to_sql("fills", c, if_exists="replace", index=False)
        return len(merged) - len(before)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
