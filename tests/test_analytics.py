import io
from pathlib import Path

import pandas as pd
import pytest

from tradebook import TradeStore, analyze, load_tradebook, merge_tradebooks, to_orders

HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time\n"
SAMPLE = Path(__file__).parent.parent / "sample" / "sample-tradebook.csv"


def book(rows):
    lines = [f"{s},X,{d},NSE,EQ,EQ,{t},false,{q},{p},{tid},{oid},{d}T{tm}"
             for s, d, t, q, p, tid, oid, tm in rows]
    return load_tradebook(io.StringIO(HEADER + "\n".join(lines)), "t.csv")


def test_fifo_partial_sell():
    f = book([
        ("ABC", "2026-01-01", "buy", 10, 100, "1", "o1", "10:00:00"),
        ("ABC", "2026-01-05", "buy", 10, 120, "2", "o2", "10:00:00"),
        ("ABC", "2026-02-01", "sell", 15, 130, "3", "o3", "10:00:00"),
    ])
    a = analyze(f)
    assert a.realized["pnl"].sum() == pytest.approx(10 * 30 + 5 * 10)
    h = a.holdings.iloc[0]
    assert h.quantity == 5 and h.avg_cost == 120


def test_sell_without_buy_is_unmatched():
    f = book([("XYZ", "2026-01-02", "sell", 5, 50, "1", "o1", "10:00:00")])
    a = analyze(f)
    assert a.realized.empty and a.holdings.empty
    assert a.unmatched_sells["quantity"].sum() == 5


def test_intraday_sell_then_buy_same_day():
    f = book([
        ("ABC", "2026-01-01", "sell", 10, 110, "1", "o1", "10:00:00"),
        ("ABC", "2026-01-01", "buy", 10, 100, "2", "o2", "14:00:00"),
    ])
    a = analyze(f)
    assert a.realized["pnl"].sum() == pytest.approx(100)
    assert a.realized["kind"].tolist() == ["intraday"]
    assert a.holdings.empty and a.unmatched_sells.empty


def test_fills_grouped_into_orders():
    f = book([
        ("ABC", "2026-01-01", "buy", 4, 100, "1", "o1", "10:00:00"),
        ("ABC", "2026-01-01", "buy", 6, 110, "2", "o1", "10:00:01"),
    ])
    o = to_orders(f)
    assert len(o) == 1 and o.iloc[0].quantity == 10
    assert o.iloc[0].avg_price == pytest.approx(106)


def test_merge_drops_duplicates():
    f = load_tradebook(SAMPLE)
    assert len(merge_tradebooks([f, f])) == len(f)


def test_store_roundtrip(tmp_path):
    s = TradeStore(tmp_path / "t.db")
    f = load_tradebook(SAMPLE)
    assert s.add(f) == len(f)
    assert s.add(f) == 0
    assert analyze(s.load()).realized["pnl"].sum() == pytest.approx(analyze(f).realized["pnl"].sum())


def test_excel_with_preamble(tmp_path):
    f = load_tradebook(SAMPLE)
    raw = pd.read_csv(SAMPLE)
    raw.columns = [c.replace("_", " ").title() for c in raw.columns]
    path = tmp_path / "tb.xlsx"
    with pd.ExcelWriter(path) as w:
        pd.DataFrame([["Client ID", "AB1234"], [None, None]]).to_excel(w, header=False, index=False)
        raw.to_excel(w, startrow=3, index=False)
    x = load_tradebook(path)
    assert len(x) == len(f)
    assert x["value"].sum() == pytest.approx(f["value"].sum())


def test_sample_totals():
    f = load_tradebook(SAMPLE)
    a = analyze(f)
    assert f["symbol"].nunique() == 10
    assert set(a.unmatched_sells["symbol"]) == {"INFY", "LT"}
    assert set(a.realized.loc[a.realized.kind == "intraday", "symbol"]) == {"ITC", "SBIN"}
    # every unit sold is either matched to a buy or flagged as unmatched
    sold = f.loc[f.trade_type == "sell", "quantity"].sum()
    assert a.realized["quantity"].sum() + a.unmatched_sells["quantity"].sum() == pytest.approx(sold)


def test_holding_periods():
    f = book([
        ("ABC", "2026-01-01", "buy", 10, 100, "1", "o1", "10:00:00"),
        ("ABC", "2026-01-10", "sell", 10, 110, "2", "o2", "11:00:00"),
        ("ABC", "2026-02-01", "buy", 5, 200, "3", "o3", "10:00:00"),
        ("ABC", "2026-02-03", "sell", 2, 190, "4", "o4", "10:00:00"),
    ])
    c = analyze(f).cycles
    assert c["status"].tolist() == ["closed", "open"]
    assert c.iloc[0].pnl == pytest.approx(100) and c.iloc[0].days == 9
    assert c.iloc[1].pnl == pytest.approx(-20) and c.iloc[1].buy_qty == 5


def test_period_summary_monthly():
    from tradebook import period_summary
    f = book([
        ("ABC", "2026-01-01", "buy", 10, 100, "1", "o1", "10:00:00"),
        ("ABC", "2026-02-10", "sell", 10, 110, "2", "o2", "11:00:00"),
    ])
    p = period_summary(f, analyze(f).realized, "M")
    assert p["buy_value"].tolist() == [1000, 0] and p["pnl"].tolist() == [0, 100]
    assert p["cumulative_pnl"].iloc[-1] == 100


def test_sample_cycles_pnl_matches_realized():
    a = analyze(load_tradebook(SAMPLE))
    assert a.cycles["pnl"].sum() == pytest.approx(a.realized["pnl"].sum())
    assert (a.cycles["status"] == "open").sum() == len(a.holdings)


def test_prices_fallback_and_valuation(monkeypatch):
    from tradebook.prices import YahooProvider, value_holdings

    quotes = {"ABC.NS": (120.0, 110.0, pd.Timestamp("2026-10-27")),
              "XYZ.BO": (50.0, 50.0, pd.Timestamp("2026-10-27"))}
    monkeypatch.setattr(YahooProvider, "_download",
                        lambda self, tickers: {t: quotes[t] for t in tickers if t in quotes})
    p = YahooProvider().fetch({"ABC": "NSE", "XYZ": "NSE", "NOPE": "NSE"})
    assert dict(zip(p.symbol, p.ticker)) == {"ABC": "ABC.NS", "XYZ": "XYZ.BO"}

    f = book([("ABC", "2026-01-01", "buy", 10, 100, "1", "o1", "10:00:00")])
    h = value_holdings(analyze(f).holdings, p).iloc[0]
    assert h.current_value == 1200 and h.unrealized_pnl == 200
    assert h.day_change == pytest.approx(100) and h.unrealized_pct == pytest.approx(20)
