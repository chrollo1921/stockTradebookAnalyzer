"""FIFO matching of buys and sells into holdings and realized P&L.

Charges (brokerage, STT, etc.) are not in the tradebook, so P&L here is gross.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Lot:
    qty: float
    price: float
    date: pd.Timestamp
    time: pd.Timestamp


@dataclass
class SymbolBook:
    symbol: str
    lots: deque = field(default_factory=deque)      # open long lots, oldest first
    shorts: deque = field(default_factory=deque)    # sells waiting for a same-day buy (intraday)
    realized: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)   # sells with no buy in the file
    cycles: list = field(default_factory=list)      # holding periods: first buy -> back to zero
    cycle: dict | None = None                       # the currently open holding period


def _close(book: SymbolBook, qty, buy: Lot, sell: Lot, kind):
    buy_price, buy_date, sell_price, sell_date = buy.price, buy.date, sell.price, sell.date
    book.realized.append({
        "symbol": book.symbol,
        "buy_date": buy_date,
        "sell_date": sell_date,
        "buy_time": buy.time,
        "sell_time": sell.time,
        "quantity": qty,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "buy_value": qty * buy_price,
        "sell_value": qty * sell_price,
        "pnl": qty * (sell_price - buy_price),
        "pnl_pct": (sell_price / buy_price - 1) * 100,
        "holding_days": (sell_date - buy_date).days,
        "kind": kind,
    })


def _expire_shorts(book: SymbolBook, before: pd.Timestamp | None):
    """Sells not covered by a buy on the same day were of shares bought before the file's range."""
    while book.shorts and (before is None or book.shorts[0].date < before):
        s = book.shorts.popleft()
        book.unmatched.append({
            "symbol": book.symbol, "sell_date": s.date, "quantity": s.qty,
            "sell_price": s.price, "sell_value": s.qty * s.price, "sell_time": s.time,
        })


def _new_cycle(book: SymbolBook, start) -> dict:
    return {"symbol": book.symbol, "opened": start, "closed": None, "buy_qty": 0.0,
            "buy_value": 0.0, "sell_qty": 0.0, "sell_value": 0.0, "pnl": 0.0, "sold_cost": 0.0, "orders": set()}


def _add_leg(cycle: dict, side: str, qty, price, order_id):
    cycle[f"{side}_qty"] += qty
    cycle[f"{side}_value"] += qty * price
    cycle["orders"].add(order_id)


def _process(book: SymbolBook, row):
    qty, price, date = float(row.quantity), float(row.price), row.trade_date
    t = row.order_execution_time
    here = Lot(qty, price, date, t)
    _expire_shorts(book, date)
    if row.trade_type == "buy":
        while qty > 1e-9 and book.shorts:          # cover an intraday short first
            s = book.shorts[0]
            m = min(qty, s.qty)
            _close(book, m, here, s, "intraday")
            c = _new_cycle(book, s.time)
            _add_leg(c, "sell", m, s.price, None)
            _add_leg(c, "buy", m, price, row.order_id)
            c.update(closed=t, pnl=m * (s.price - price), sold_cost=m * price)
            book.cycles.append(c)
            s.qty -= m
            qty -= m
            if s.qty <= 1e-9:
                book.shorts.popleft()
        if qty > 1e-9:
            book.lots.append(Lot(qty, price, date, t))
            if book.cycle is None:
                book.cycle = _new_cycle(book, t)
            _add_leg(book.cycle, "buy", qty, price, row.order_id)
    else:
        while qty > 1e-9 and book.lots:
            lot = book.lots[0]
            m = min(qty, lot.qty)
            kind = "intraday" if lot.date == date else "delivery"
            _close(book, m, lot, here, kind)
            _add_leg(book.cycle, "sell", m, price, row.order_id)
            book.cycle["pnl"] += m * (price - lot.price)
            book.cycle["sold_cost"] += m * lot.price
            lot.qty -= m
            qty -= m
            if lot.qty <= 1e-9:
                book.lots.popleft()
        if not book.lots and book.cycle is not None:
            book.cycle["closed"] = t
            book.cycles.append(book.cycle)
            book.cycle = None
        if qty > 1e-9:
            book.shorts.append(Lot(qty, price, date, t))


@dataclass
class Analysis:
    summary: pd.DataFrame
    holdings: pd.DataFrame
    realized: pd.DataFrame
    unmatched_sells: pd.DataFrame
    cycles: pd.DataFrame


def analyze(fills: pd.DataFrame) -> Analysis:
    books: dict[str, SymbolBook] = {}
    ordered = fills.sort_values(["order_execution_time", "trade_id"], kind="stable")
    for row in ordered.itertuples(index=False):
        book = books.setdefault(row.symbol, SymbolBook(row.symbol))
        _process(book, row)
    for book in books.values():
        _expire_shorts(book, None)
        if book.cycle is not None:
            book.cycles.append(book.cycle)
            book.cycle = None

    realized = pd.DataFrame([r for b in books.values() for r in b.realized],
                            columns=["symbol", "buy_date", "sell_date", "buy_time", "sell_time",
                                     "quantity", "buy_price",
                                     "sell_price", "buy_value", "sell_value", "pnl", "pnl_pct",
                                     "holding_days", "kind"])
    unmatched = pd.DataFrame([u for b in books.values() for u in b.unmatched],
                             columns=["symbol", "sell_date", "sell_time", "quantity", "sell_price",
                                      "sell_value"])
    cycles = _cycles_frame([c for b in books.values() for c in b.cycles])

    holding_rows = []
    for b in books.values():
        qty = sum(l.qty for l in b.lots)
        if qty > 1e-9:
            invested = sum(l.qty * l.price for l in b.lots)
            holding_rows.append({
                "symbol": b.symbol, "quantity": qty, "avg_cost": invested / qty,
                "invested": invested, "first_buy": b.lots[0].date, "last_buy": b.lots[-1].date,
            })
    holdings = pd.DataFrame(holding_rows, columns=["symbol", "quantity", "avg_cost", "invested",
                                                   "first_buy", "last_buy"])

    summary = _summary(fills, realized, unmatched, holdings)
    return Analysis(summary, holdings.sort_values("invested", ascending=False, ignore_index=True),
                    realized.sort_values("sell_time", ignore_index=True),
                    unmatched.sort_values("sell_time", ignore_index=True),
                    cycles)


def _cycles_frame(rows) -> pd.DataFrame:
    """One row per holding period of a stock, from the first buy until the position is back to zero."""
    cols = ["symbol", "opened", "closed", "status", "days", "buy_qty", "avg_buy", "buy_value",
            "sell_qty", "avg_sell", "sell_value", "pnl", "pnl_pct", "orders"]
    if not rows:
        return pd.DataFrame(columns=cols)
    c = pd.DataFrame(rows)
    c["orders"] = c["orders"].map(lambda o: len(o - {None}) + (None in o))
    c["status"] = c["closed"].map(lambda x: "open" if x is None or pd.isna(x) else "closed")
    c["closed"] = pd.to_datetime(c["closed"])
    c["days"] = (c["closed"].dt.normalize() - pd.to_datetime(c["opened"]).dt.normalize()).dt.days
    c["avg_buy"] = c["buy_value"] / c["buy_qty"].where(c["buy_qty"] > 0)
    c["avg_sell"] = c["sell_value"] / c["sell_qty"].where(c["sell_qty"] > 0)
    c["pnl_pct"] = c["pnl"] / c["sold_cost"].where(c["sold_cost"] > 0) * 100
    return c[cols].sort_values("opened", ignore_index=True)


def period_summary(fills: pd.DataFrame, realized: pd.DataFrame, freq: str = "M") -> pd.DataFrame:
    """Buys, sells and realized P&L per period. freq: 'D', 'W' or 'M'.

    P&L is booked in the period of the sell.
    """
    f = fills.assign(period=fills["trade_date"].dt.to_period(freq).dt.start_time)
    side = f.pivot_table(index="period", columns="trade_type", values="value", aggfunc="sum",
                         fill_value=0)
    orders = f.groupby(["period", "trade_type"])["order_id"].nunique().unstack(fill_value=0)
    out = pd.DataFrame(index=side.index)
    for t in ["buy", "sell"]:
        out[f"{t}_orders"] = orders[t] if t in orders else 0
        out[f"{t}_value"] = side[t] if t in side else 0.0
    if len(realized):
        r = realized.assign(period=realized["sell_date"].dt.to_period(freq).dt.start_time)
        out["pnl"] = r.groupby("period")["pnl"].sum()
    else:
        out["pnl"] = 0.0
    out["pnl"] = out["pnl"].fillna(0.0)
    out["stocks"] = f.groupby("period")["symbol"].nunique()
    out = out.sort_index()
    out["cumulative_pnl"] = out["pnl"].cumsum()
    return out.reset_index()


def _summary(fills, realized, unmatched, holdings) -> pd.DataFrame:
    side = fills.pivot_table(index="symbol", columns="trade_type", values=["quantity", "value"],
                             aggfunc="sum", fill_value=0)
    side.columns = [f"{t}_{m}" for m, t in side.columns]
    for c in ["buy_quantity", "buy_value", "sell_quantity", "sell_value"]:
        if c not in side:
            side[c] = 0.0
    s = side[["buy_quantity", "buy_value", "sell_quantity", "sell_value"]].copy()
    s["avg_buy"] = s["buy_value"] / s["buy_quantity"].where(s["buy_quantity"] > 0)
    s["avg_sell"] = s["sell_value"] / s["sell_quantity"].where(s["sell_quantity"] > 0)
    s["realized_pnl"] = realized.groupby("symbol")["pnl"].sum()
    s["unmatched_sell_qty"] = unmatched.groupby("symbol")["quantity"].sum()
    h = holdings.set_index("symbol")
    s["open_qty"] = h["quantity"]
    s["open_avg_cost"] = h["avg_cost"]
    s["trades"] = fills.groupby("symbol")["order_id"].nunique()
    s["first_trade"] = fills.groupby("symbol")["trade_date"].min()
    s["last_trade"] = fills.groupby("symbol")["trade_date"].max()
    s[["realized_pnl", "unmatched_sell_qty", "open_qty"]] = (
        s[["realized_pnl", "unmatched_sell_qty", "open_qty"]].fillna(0))
    return s.reset_index().sort_values("symbol", ignore_index=True)
