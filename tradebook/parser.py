"""Load Zerodha (Console) equity tradebook exports into a normalized DataFrame."""
from __future__ import annotations

import io
from pathlib import Path
from typing import BinaryIO, Union

import pandas as pd

REQUIRED_COLUMNS = [
    "symbol", "trade_date", "exchange", "trade_type",
    "quantity", "price", "trade_id", "order_id",
]
OPTIONAL_COLUMNS = ["isin", "segment", "series", "auction", "order_execution_time"]

# Console sometimes uses human-readable headers ("Trade Date") in Excel exports.
_ALIASES = {
    "trade date": "trade_date",
    "trade type": "trade_type",
    "trade id": "trade_id",
    "order id": "order_id",
    "order execution time": "order_execution_time",
    "qty": "quantity",
}

Source = Union[str, Path, BinaryIO]


def _read_raw(source: Source, filename: str | None = None) -> pd.DataFrame:
    name = (filename or getattr(source, "name", None) or str(source)).lower()
    if name.endswith((".xlsx", ".xls")):
        raw = pd.read_excel(source, header=None, dtype=str)
        # Console Excel files have a few lines of client info above the table:
        # find the row that holds the real header.
        header_row = next(
            (i for i, row in raw.iterrows()
             if {"symbol", "trade_type"} <= {_norm(c) for c in row if isinstance(c, str)}),
            None,
        )
        if header_row is None:
            raise ValueError("Could not find a header row with 'Symbol' and 'Trade Type'.")
        df = raw.iloc[header_row + 1:].copy()
        df.columns = list(raw.iloc[header_row])
        return df.dropna(how="all")
    return pd.read_csv(source, dtype=str)


def _norm(col: str) -> str:
    c = str(col).strip().lower()
    return _ALIASES.get(c, c.replace(" ", "_"))


def load_tradebook(source: Source, filename: str | None = None) -> pd.DataFrame:
    """Read a tradebook CSV/XLSX and return one row per fill with typed columns."""
    df = _read_raw(source, filename)
    df.columns = [_norm(c) for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c and c != "nan"]]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")
    for c in OPTIONAL_COLUMNS:
        if c not in df.columns:
            df[c] = None

    df = df.dropna(subset=["symbol", "trade_type"])
    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["exchange"] = df["exchange"].str.strip().str.upper()
    df["trade_type"] = df["trade_type"].str.strip().str.lower()
    bad = ~df["trade_type"].isin(["buy", "sell"])
    if bad.any():
        raise ValueError(f"Unknown trade_type values: {sorted(df.loc[bad, 'trade_type'].unique())}")

    df["quantity"] = pd.to_numeric(df["quantity"], errors="raise")
    df["price"] = pd.to_numeric(df["price"], errors="raise")
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
    exec_time = pd.to_datetime(df["order_execution_time"], errors="coerce")
    df["order_execution_time"] = exec_time.fillna(df["trade_date"])
    df["trade_id"] = df["trade_id"].astype(str).str.strip()
    df["order_id"] = df["order_id"].astype(str).str.strip()
    df["value"] = df["quantity"] * df["price"]

    cols = REQUIRED_COLUMNS + OPTIONAL_COLUMNS + ["value"]
    return df[cols].sort_values(["order_execution_time", "trade_id"]).reset_index(drop=True)


def merge_tradebooks(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine several exports (e.g. different date ranges), dropping duplicate fills."""
    if not frames:
        return pd.DataFrame(columns=REQUIRED_COLUMNS + OPTIONAL_COLUMNS + ["value"])
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["exchange", "symbol", "trade_id", "order_id"])
    return df.sort_values(["order_execution_time", "trade_id"]).reset_index(drop=True)


def to_orders(fills: pd.DataFrame) -> pd.DataFrame:
    """Collapse multiple fills of the same order into one row with a weighted avg price."""
    g = fills.groupby(["order_id", "symbol", "exchange", "trade_type"], as_index=False)
    orders = g.agg(
        trade_date=("trade_date", "first"),
        time=("order_execution_time", "min"),
        quantity=("quantity", "sum"),
        value=("value", "sum"),
        fills=("trade_id", "count"),
    )
    orders["avg_price"] = orders["value"] / orders["quantity"]
    return orders.sort_values("time").reset_index(drop=True)


def read_bytes(data: bytes, filename: str) -> pd.DataFrame:
    return load_tradebook(io.BytesIO(data), filename)
