"""Current prices for held stocks.

v1 uses Yahoo Finance (free, no login, ~15 min delayed during market hours).
A Kite Connect provider can replace it later: anything with a `fetch(symbols)` method works.
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd

PRICE_COLUMNS = ["symbol", "ltp", "prev_close", "as_of", "ticker"]


class PriceProvider(Protocol):
    def fetch(self, symbols: dict[str, str]) -> pd.DataFrame:
        """symbols maps tradebook symbol -> exchange (NSE/BSE). Returns PRICE_COLUMNS rows."""


class YahooProvider:
    SUFFIX = {"NSE": ".NS", "BSE": ".BO"}

    def _download(self, tickers: list[str]) -> dict[str, tuple[float, float, pd.Timestamp]]:
        import yfinance as yf

        if not tickers:
            return {}
        data = yf.download(tickers, period="5d", interval="1d", group_by="ticker",
                           auto_adjust=False, progress=False, threads=True)
        out = {}
        for t in tickers:
            try:
                close = (data[t] if isinstance(data.columns, pd.MultiIndex) else data)["Close"].dropna()
            except KeyError:
                continue
            if close.empty:
                continue
            prev = float(close.iloc[-2]) if len(close) > 1 else float("nan")
            out[t] = (float(close.iloc[-1]), prev, close.index[-1])
        return out

    def fetch(self, symbols: dict[str, str]) -> pd.DataFrame:
        # Try the exchange the stock was traded on first, then the other one.
        first = {s: f"{s}{self.SUFFIX.get(ex, '.NS')}" for s, ex in symbols.items()}
        got = self._download(list(first.values()))
        retry = {s: f"{s}{'.BO' if t.endswith('.NS') else '.NS'}"
                 for s, t in first.items() if t not in got}
        got.update(self._download(list(retry.values())))

        rows = []
        for s in symbols:
            t = first[s] if first[s] in got else retry.get(s)
            if t in got:
                ltp, prev, when = got[t]
                rows.append({"symbol": s, "ltp": ltp, "prev_close": prev, "as_of": when, "ticker": t})
        return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def last_exchange(fills: pd.DataFrame, symbols) -> dict[str, str]:
    """Exchange of the most recent trade of each symbol."""
    f = fills[fills["symbol"].isin(list(symbols))].sort_values("order_execution_time")
    return f.groupby("symbol")["exchange"].last().to_dict()


def value_holdings(holdings: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Add current price, value, unrealized P&L and today's change to the holdings table."""
    h = holdings.merge(prices[["symbol", "ltp", "prev_close", "as_of"]], on="symbol", how="left")
    h["current_value"] = h["quantity"] * h["ltp"]
    h["unrealized_pnl"] = h["current_value"] - h["invested"]
    h["unrealized_pct"] = h["unrealized_pnl"] / h["invested"] * 100
    h["day_change_pct"] = (h["ltp"] / h["prev_close"] - 1) * 100
    h["day_change"] = h["quantity"] * (h["ltp"] - h["prev_close"])
    return h
