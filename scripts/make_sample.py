"""Generate sample/sample-tradebook.csv: a made-up tradebook in Zerodha's format.

Prices, quantities and IDs are random, not real trades. It covers the cases the app
handles: multi-fill orders, partial sells, sells with no earlier buy, intraday, NSE + BSE.
"""
import csv
import random
import zlib
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)
STOCKS = {"RELIANCE": 1300, "TCS": 3500, "INFY": 1500, "HDFCBANK": 1650, "ITC": 420,
          "SBIN": 800, "TATAMOTORS": 700, "WIPRO": 280, "LT": 3600, "BHARTIARTL": 1600}
HEADER = ["symbol", "isin", "trade_date", "exchange", "segment", "series", "trade_type", "auction",
          "quantity", "price", "trade_id", "order_id", "order_execution_time"]

rows, next_trade, next_order = [], 100000, 9000000000000000


def order(sym, when, side, qty, exchange="NSE", fills=1):
    global next_trade, next_order
    next_order += random.randint(1, 999)
    base = STOCKS[sym] * random.uniform(0.85, 1.2)
    parts = [qty // fills] * fills
    parts[-1] += qty - sum(parts)
    for i, q in enumerate(parts):
        next_trade += random.randint(1, 99)
        t = when + timedelta(seconds=i)
        rows.append([sym, f"INE{zlib.crc32(sym.encode()) % 10**6:06d}01", t.date().isoformat(), exchange, "EQ",
                     "EQ", side, "false", f"{q:.6f}", f"{base * random.uniform(0.999, 1.001):.6f}",
                     next_trade, next_order, t.isoformat(timespec="seconds")])


start = datetime(2026, 4, 1, 9, 30)
day = lambda n, h=10, m=0: start + timedelta(days=n, hours=h - 9, minutes=m - 30)

order("RELIANCE", day(0), "buy", 10)
order("RELIANCE", day(20), "buy", 5, fills=2)
order("RELIANCE", day(60), "sell", 8)                 # partial sell, FIFO
order("TCS", day(2), "buy", 4)
order("TCS", day(90), "sell", 4, exchange="BSE")      # closed position, sold on BSE
order("INFY", day(5), "sell", 12)                     # sold without a buy in range
order("HDFCBANK", day(7), "buy", 30, fills=3)
order("HDFCBANK", day(40), "buy", 20)
order("ITC", day(10, 9, 45), "buy", 100)
order("ITC", day(10, 14, 50), "sell", 100)            # intraday
order("SBIN", day(12, 11), "sell", 25)                # intraday short: sell then buy same day
order("SBIN", day(12, 15), "buy", 25)
order("TATAMOTORS", day(15), "buy", 40)
order("TATAMOTORS", day(75), "sell", 40, fills=2)
order("TATAMOTORS", day(100), "buy", 20)              # re-entry: second holding period
order("WIPRO", day(30), "buy", 50)
order("LT", day(35), "sell", 3)                       # sold without a buy in range
order("BHARTIARTL", day(50), "buy", 6)
order("BHARTIARTL", day(120), "sell", 2)

rows.sort(key=lambda r: r[-1])
out = Path(__file__).resolve().parent.parent / "sample" / "sample-tradebook.csv"
with open(out, "w", newline="") as f:
    csv.writer(f).writerows([HEADER, *rows])
print(f"wrote {len(rows)} rows to {out}")
