"""Streamlit UI for the Zerodha tradebook analyzer.  Run: streamlit run app.py"""
import altair as alt
import pandas as pd
import streamlit as st

from tradebook import TradeStore, analyze, period_summary, to_orders
from tradebook.parser import read_bytes
from tradebook.prices import YahooProvider, last_exchange, value_holdings

st.set_page_config(page_title="Tradebook Analyzer", page_icon="📈", layout="wide")
store = TradeStore()

INR = "₹{:,.2f}"


DT = "{:%d %b %Y %H:%M}"
GREEN, RED, BLUE, ORANGE = "#16a34a", "#dc2626", "#2563eb", "#f97316"


def money(df, cols):
    return {c: INR for c in cols if c in df.columns}


def pnl_color(v):
    if not isinstance(v, (int, float)) or pd.isna(v):
        return ""
    return f"color: {GREEN}" if v > 0 else f"color: {RED}" if v < 0 else ""


def side_color(v):
    return f"color: {BLUE}" if v == "buy" else f"color: {ORANGE}" if v == "sell" else ""


# ---------- sidebar: import ----------
with st.sidebar:
    st.header("Import tradebook")
    st.caption("Zerodha Console → Reports → Tradebook → Equity. CSV or Excel. "
               "Upload several date ranges; duplicates are ignored.")
    files = st.file_uploader("Tradebook files", type=["csv", "xlsx", "xls"],
                             accept_multiple_files=True)
    if files and st.button("Import", type="primary", width="stretch"):
        added = 0
        for f in files:
            try:
                added += store.add(read_bytes(f.getvalue(), f.name))
            except Exception as e:
                st.error(f"{f.name}: {e}")
        st.success(f"Imported {added} new fills.")

    fills_all = store.load()
    st.divider()
    st.caption(f"{len(fills_all)} fills stored locally")
    if len(fills_all) and st.button("Clear stored history", width="stretch"):
        store.clear()
        st.rerun()

if fills_all.empty:
    st.title("📈 Tradebook Analyzer")
    st.info("Upload a Zerodha equity tradebook (CSV or Excel) from the sidebar to get started.")
    st.stop()

# ---------- filters ----------
with st.sidebar:
    st.header("Filter")
    dmin, dmax = fills_all["trade_date"].min().date(), fills_all["trade_date"].max().date()
    date_range = st.date_input("Trade dates", (dmin, dmax), min_value=dmin, max_value=dmax)
    symbols = st.multiselect("Stocks", sorted(fills_all["symbol"].unique()))
    st.header("Current prices")
    live = st.toggle("Fetch current prices", value=True,
                     help="From Yahoo Finance, free, about 15 minutes delayed during market hours.")
    if live and st.button("Refresh prices", width="stretch"):
        st.cache_data.clear()

fills = fills_all
if isinstance(date_range, tuple) and len(date_range) == 2:
    lo, hi = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    fills = fills[(fills["trade_date"] >= lo) & (fills["trade_date"] <= hi)]
if symbols:
    fills = fills[fills["symbol"].isin(symbols)]
if fills.empty:
    st.warning("No trades match the filters.")
    st.stop()

a = analyze(fills)
orders = to_orders(fills)


@st.cache_data(ttl=300, show_spinner="Fetching current prices…")
def get_prices(items: tuple) -> pd.DataFrame:
    return YahooProvider().fetch(dict(items))


prices = pd.DataFrame(columns=["symbol", "ltp", "prev_close", "as_of", "ticker"])
price_error = None
if live and len(a.holdings):
    try:
        prices = get_prices(tuple(sorted(last_exchange(fills, a.holdings["symbol"]).items())))
    except Exception as e:  # network down, Yahoo changed, yfinance missing…
        price_error = str(e)
manual = st.session_state.setdefault("manual_prices", {})
if manual:
    extra = pd.DataFrame([{"symbol": k, "ltp": v, "prev_close": float("nan"), "as_of": pd.NaT,
                           "ticker": "manual"} for k, v in manual.items()
                          if k not in set(prices["symbol"])])
    prices = pd.concat([prices, extra], ignore_index=True) if len(extra) else prices
hv = value_holdings(a.holdings, prices)
priced = hv["ltp"].notna()

# ---------- headline ----------
st.title("📈 Tradebook Analyzer")
st.caption(f"{fills['trade_date'].min():%d %b %Y} – {fills['trade_date'].max():%d %b %Y} · "
           f"{fills['symbol'].nunique()} stocks · {len(orders)} orders ({len(fills)} fills)")

buys = fills[fills.trade_type == "buy"]["value"].sum()
sells = fills[fills.trade_type == "sell"]["value"].sum()
realized = a.realized["pnl"].sum()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total bought", INR.format(buys))
c2.metric("Total sold", INR.format(sells))
c3.metric("Realized P&L (matched)", INR.format(realized))
if priced.any():
    c4.metric(f"Holdings value · {len(a.holdings)} stocks",
              INR.format(hv.loc[priced, "current_value"].sum() + hv.loc[~priced, "invested"].sum()),
              f"{INR.format(hv['unrealized_pnl'].sum())} unrealized")
else:
    c4.metric(f"Open holdings at cost · {len(a.holdings)} stocks",
              INR.format(a.holdings["invested"].sum()))
c5.metric(f"Sold w/o buy in file · {a.unmatched_sells['symbol'].nunique()} stocks",
          INR.format(a.unmatched_sells["sell_value"].sum()))

if not a.unmatched_sells.empty:
    st.caption("ⓘ Some sells have no matching buy in the file (shares bought before this date range). "
               "Their P&L can't be computed; import an older tradebook to include them. "
               "P&L is gross: charges and taxes aren't in the tradebook.")

def render_stock_detail(sym: str):
    so = orders[orders.symbol == sym]
    row = a.summary.set_index("symbol").loc[sym]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Bought", f"{row.buy_quantity:g} @ " + (INR.format(row.avg_buy) if row.buy_quantity else "–"))
    m2.metric("Sold", f"{row.sell_quantity:g} @ " + (INR.format(row.avg_sell) if row.sell_quantity else "–"))
    m3.metric("Net realized P&L", INR.format(row.realized_pnl))
    m4.metric("Holding now", f"{row.open_qty:g}" + (f" @ {INR.format(row.open_avg_cost)}" if row.open_qty else ""))
    hrow = hv[hv.symbol == sym]
    if len(hrow) and pd.notna(hrow.iloc[0].ltp):
        hr = hrow.iloc[0]
        n1, n2, n3, n4 = st.columns(4)
        n1.metric("Current price", INR.format(hr.ltp),
                  f"{hr.day_change_pct:+.2f}% today" if pd.notna(hr.day_change_pct) else None)
        n2.metric("Current value", INR.format(hr.current_value))
        n3.metric("Unrealized P&L", INR.format(hr.unrealized_pnl), f"{hr.unrealized_pct:+.2f}%")
        n4.metric("Total P&L (realized + unrealized)", INR.format(row.realized_pnl + hr.unrealized_pnl))

    st.altair_chart(
        alt.Chart(so).mark_point(filled=True, size=140, opacity=0.9).encode(
            x=alt.X("time:T", title=None),
            y=alt.Y("avg_price:Q", title="Price (₹)", scale=alt.Scale(zero=False, padding=20)),
            color=alt.Color("trade_type:N", title=None,
                            scale=alt.Scale(domain=["buy", "sell"], range=[BLUE, ORANGE])),
            shape=alt.Shape("trade_type:N", legend=None,
                            scale=alt.Scale(domain=["buy", "sell"], range=["triangle-up", "triangle-down"])),
            tooltip=[alt.Tooltip("time:T", title="Time", format="%d %b %Y %H:%M"),
                     alt.Tooltip("trade_type:N", title="Side"), alt.Tooltip("quantity:Q", title="Qty"),
                     alt.Tooltip("avg_price:Q", title="Price", format=",.2f"),
                     alt.Tooltip("fills:Q", title="Fills")]).properties(height=260),
        width="stretch")

    st.markdown("**Holding periods** — from the first buy until the position is back to zero")
    cy = a.cycles[a.cycles.symbol == sym]
    if cy.empty:
        st.caption("No buys of this stock in the data (only sells of shares bought earlier).")
    else:
        last_day = fills["trade_date"].max()
        days = cy["days"].where(cy["status"] == "closed",
                                (last_day - cy["opened"].dt.normalize()).dt.days)
        cyt = pd.DataFrame({
            "Opened": cy["opened"].dt.strftime("%d %b %Y %H:%M"),
            "Closed": cy["closed"].dt.strftime("%d %b %Y %H:%M").fillna("still holding"),
            "Days": days.astype(int),
            "Bought": cy["buy_qty"], "Avg buy": cy["avg_buy"],
            "Sold": cy["sell_qty"], "Avg sell": cy["avg_sell"],
            "Net P&L": cy["pnl"], "P&L %": cy["pnl_pct"], "Status": cy["status"]})
        st.dataframe(cyt.style.format({"Bought": "{:g}",
                                       "Sold": "{:g}", "Avg buy": INR, "Avg sell": INR, "Net P&L": INR,
                                       "P&L %": "{:+.2f}%"}, na_rep="–")
                     .map(pnl_color, subset=["Net P&L", "P&L %"]),
                     hide_index=True, width="stretch")

    st.markdown("**Buy → sell matches** (FIFO)")
    rs = a.realized[a.realized.symbol == sym]
    if rs.empty:
        st.caption("Nothing sold against a buy in this data yet.")
    else:
        rst = pd.DataFrame({
            "Bought at": rs["buy_time"], "Sold at": rs["sell_time"], "Qty": rs["quantity"],
            "Buy price": rs["buy_price"], "Sell price": rs["sell_price"], "P&L": rs["pnl"],
            "P&L %": rs["pnl_pct"], "Days held": rs["holding_days"], "Type": rs["kind"]})
        st.dataframe(rst.style.format({"Bought at": DT, "Sold at": DT, "Qty": "{:g}", "Buy price": INR,
                                       "Sell price": INR, "P&L": INR, "P&L %": "{:+.2f}%"})
                     .map(pnl_color, subset=["P&L", "P&L %"]),
                     hide_index=True, width="stretch")

    us = a.unmatched_sells[a.unmatched_sells.symbol == sym]
    if not us.empty:
        st.caption(f"ⓘ {us['quantity'].sum():g} shares sold for {INR.format(us['sell_value'].sum())} "
                   "have no buy in the data (bought before this range), so they're not in the P&L.")

    st.markdown("**All orders**")
    ot = pd.DataFrame({"Time": so["time"], "Side": so["trade_type"], "Qty": so["quantity"],
                       "Avg price": so["avg_price"], "Value": so["value"], "Fills": so["fills"],
                       "Exchange": so["exchange"], "Order ID": so["order_id"]})
    st.dataframe(ot.style.format({"Time": DT, "Qty": "{:g}", "Avg price": INR, "Value": INR})
                 .map(side_color, subset=["Side"]),
                 hide_index=True, width="stretch")
    with st.expander("Individual fills"):
        ft = fills[fills.symbol == sym]
        st.dataframe(pd.DataFrame({"Time": ft["order_execution_time"], "Side": ft["trade_type"],
                                   "Qty": ft["quantity"], "Price": ft["price"], "Value": ft["value"],
                                   "Trade ID": ft["trade_id"], "Order ID": ft["order_id"]})
                     .style.format({"Time": DT, "Qty": "{:g}", "Price": INR, "Value": INR})
                     .map(side_color, subset=["Side"]),
                     hide_index=True, width="stretch")


@st.dialog("Stock details", width="large")
def stock_dialog(sym: str):
    st.subheader(sym)
    render_stock_detail(sym)


tab_sum, tab_period, tab_hold, tab_pnl, tab_stock, tab_trades = st.tabs(
    ["Per-stock summary", "By time period", "Holdings", "Realized P&L", "Stock detail", "All trades"])

with tab_sum:
    st.caption("Click a row to see that stock's detailed trades.")
    s = a.summary.rename(columns={
        "symbol": "Stock", "buy_quantity": "Bought qty", "avg_buy": "Avg buy",
        "buy_value": "Buy value", "sell_quantity": "Sold qty", "avg_sell": "Avg sell",
        "sell_value": "Sell value", "realized_pnl": "Realized P&L", "open_qty": "Open qty",
        "open_avg_cost": "Open avg cost", "unmatched_sell_qty": "Sold w/o buy",
        "trades": "Orders", "first_trade": "First", "last_trade": "Last"})
    s = s[["Stock", "Bought qty", "Avg buy", "Buy value", "Sold qty", "Avg sell", "Sell value",
           "Realized P&L", "Open qty", "Open avg cost", "Sold w/o buy", "Orders", "First", "Last"]]
    event = st.dataframe(
        s.style.format({**money(s, ["Avg buy", "Buy value", "Avg sell", "Sell value",
                                    "Realized P&L", "Open avg cost"]),
                        "First": "{:%d %b %y}", "Last": "{:%d %b %y}", "Bought qty": "{:g}",
                        "Sold qty": "{:g}", "Open qty": "{:g}", "Sold w/o buy": "{:g}"}, na_rep="–")
         .map(pnl_color, subset=["Realized P&L"]),
        hide_index=True, width="stretch", height=600,
        on_select="rerun", selection_mode="single-row", key="summary_table")
    picked = event.selection.rows
    picked_sym = s.iloc[picked[0]]["Stock"] if picked else None
    # open the dialog only when the selection changes, not on every rerun
    if picked_sym and picked_sym != st.session_state.get("last_dialog"):
        st.session_state["last_dialog"] = picked_sym
        st.session_state["detail_symbol"] = picked_sym
        stock_dialog(picked_sym)
    elif not picked_sym:
        st.session_state.pop("last_dialog", None)

with tab_period:
    freq_label = st.segmented_control("Group by", ["Day", "Week", "Month"], default="Month",
                                      key="period_freq") or "Month"
    freq = {"Day": "D", "Week": "W", "Month": "M"}[freq_label]
    ps = period_summary(fills, a.realized, freq)
    label_fmt = {"D": "%d %b %Y", "W": "Week of %d %b %Y", "M": "%b %Y"}[freq]
    ps["label"] = ps["period"].dt.strftime(label_fmt)

    p1, p2, p3 = st.columns(3)
    best = ps.loc[ps["pnl"].idxmax()] if ps["pnl"].any() else None
    worst = ps.loc[ps["pnl"].idxmin()] if ps["pnl"].any() else None
    p1.metric("Net realized P&L", INR.format(ps["pnl"].sum()))
    p2.metric("Best period", best["label"] if best is not None else "–",
              INR.format(best["pnl"]) if best is not None else None)
    p3.metric("Worst period", worst["label"] if worst is not None else "–",
              INR.format(worst["pnl"]) if worst is not None else None)

    flows = ps.melt(id_vars=["period", "label"], value_vars=["buy_value", "sell_value"],
                    var_name="side", value_name="amount")
    flows["side"] = flows["side"].map({"buy_value": "Bought", "sell_value": "Sold"})
    xlab = alt.X("label:N", title=None, sort=ps["label"].tolist(), axis=alt.Axis(labelAngle=-40))
    base = alt.Chart(ps).encode(x=xlab)
    st.altair_chart(
        alt.Chart(flows).mark_bar().encode(
            x=xlab,
            xOffset="side:N",
            y=alt.Y("amount:Q", title="Value (₹)"),
            color=alt.Color("side:N", title=None,
                            scale=alt.Scale(domain=["Bought", "Sold"], range=[BLUE, ORANGE])),
            tooltip=["label", "side", alt.Tooltip("amount:Q", format=",.0f")]
        ).properties(height=240, title="Buys and sells"),
        width="stretch")
    st.altair_chart(
        alt.layer(
            base.mark_bar().encode(
                y=alt.Y("pnl:Q", title="Realized P&L (₹)"),
                color=alt.condition("datum.pnl >= 0", alt.value(GREEN), alt.value(RED)),
                tooltip=["label", alt.Tooltip("pnl:Q", title="Net P&L", format=",.2f")]),
            base.mark_line(color="#64748b", point=True).encode(
                y="cumulative_pnl:Q",
                tooltip=["label", alt.Tooltip("cumulative_pnl:Q", title="Cumulative", format=",.2f")]),
        ).properties(height=240, title="Net P&L per period (bars) and cumulative (line)"),
        width="stretch")

    pt = pd.DataFrame({"Period": ps["label"], "Stocks traded": ps["stocks"],
                       "Buy orders": ps["buy_orders"], "Bought (₹)": ps["buy_value"],
                       "Sell orders": ps["sell_orders"], "Sold (₹)": ps["sell_value"],
                       "Net P&L": ps["pnl"], "Cumulative P&L": ps["cumulative_pnl"]})
    st.dataframe(pt.style.format(money(pt, ["Bought (₹)", "Sold (₹)", "Net P&L", "Cumulative P&L"]))
                 .map(pnl_color, subset=["Net P&L", "Cumulative P&L"]),
                 hide_index=True, width="stretch")
    st.caption("P&L is counted in the period of the sell. Pick a period below to see its orders.")

    pick = st.selectbox("Orders in period", ps["label"].tolist()[::-1], key="period_pick")
    start = ps.loc[ps["label"] == pick, "period"].iloc[0]
    end = start + {"D": pd.DateOffset(days=1), "W": pd.DateOffset(weeks=1),
                   "M": pd.DateOffset(months=1)}[freq]
    po = orders[(orders["trade_date"] >= start) & (orders["trade_date"] < end)]
    pr = a.realized[(a.realized["sell_date"] >= start) & (a.realized["sell_date"] < end)]
    pnl_by_stock = pr.groupby("symbol")["pnl"].sum()
    st.dataframe(pd.DataFrame({"Time": po["time"], "Stock": po["symbol"], "Side": po["trade_type"],
                               "Qty": po["quantity"], "Avg price": po["avg_price"], "Value": po["value"]})
                 .style.format({"Time": DT, "Qty": "{:g}", "Avg price": INR, "Value": INR})
                 .map(side_color, subset=["Side"]),
                 hide_index=True, width="stretch")
    if len(pnl_by_stock):
        st.caption("Realized in this period: " + ", ".join(
            f"{k} {INR.format(v)}" for k, v in pnl_by_stock.sort_values(ascending=False).items()))

with tab_hold:
    if price_error:
        st.warning(f"Couldn't fetch current prices: {price_error}")
    if priced.any():
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Invested", INR.format(hv.loc[priced, "invested"].sum()))
        k2.metric("Current value", INR.format(hv.loc[priced, "current_value"].sum()))
        upnl = hv.loc[priced, "unrealized_pnl"].sum()
        k3.metric("Unrealized P&L", INR.format(upnl),
                  f"{upnl / hv.loc[priced, 'invested'].sum() * 100:+.2f}%")
        k4.metric("Today's change", INR.format(hv["day_change"].sum()))
    h = pd.DataFrame({"Stock": hv["symbol"], "Qty": hv["quantity"], "Avg cost": hv["avg_cost"],
                      "Current price": hv["ltp"], "Today %": hv["day_change_pct"],
                      "Invested": hv["invested"], "Current value": hv["current_value"],
                      "Unrealized P&L": hv["unrealized_pnl"], "Unrealized %": hv["unrealized_pct"],
                      "First buy": hv["first_buy"]})
    if priced.any():
        h = h.sort_values("Unrealized P&L", ascending=False, na_position="last")
    st.dataframe(h.style.format({**money(h, ["Avg cost", "Current price", "Invested",
                                             "Current value", "Unrealized P&L"]),
                                 "Qty": "{:g}", "Today %": "{:+.2f}%", "Unrealized %": "{:+.2f}%",
                                 "First buy": "{:%d %b %y}"}, na_rep="–")
                 .map(pnl_color, subset=["Today %", "Unrealized P&L", "Unrealized %"]),
                 hide_index=True, width="stretch", height=min(600, 38 + 35 * len(h)))
    if priced.any():
        chart = hv[priced].assign(Stock=hv["symbol"])
        st.altair_chart(
            alt.Chart(chart).mark_bar().encode(
                x=alt.X("unrealized_pnl:Q", title="Unrealized P&L (₹)"),
                y=alt.Y("Stock:N", sort="-x", title=None),
                color=alt.condition("datum.unrealized_pnl >= 0", alt.value(GREEN), alt.value(RED)),
                tooltip=["Stock", alt.Tooltip("unrealized_pnl:Q", title="Unrealized", format=",.2f"),
                         alt.Tooltip("unrealized_pct:Q", title="%", format="+.2f")]
            ).properties(height=max(200, 18 * int(priced.sum()))),
            width="stretch")
        as_of = pd.to_datetime(prices["as_of"]).max()
        st.caption(f"Prices from Yahoo Finance{f' as of {as_of:%d %b %Y}' if pd.notna(as_of) else ''}, "
                   "about 15 minutes delayed during market hours.")
    missing = hv.loc[~priced, "symbol"].tolist()
    if missing:
        with st.expander(f"{len(missing)} stocks without a price — enter manually"):
            st.caption("Yahoo Finance may not list some stocks (new listings, SME, renamed symbols).")
            ed = st.data_editor(pd.DataFrame({"Stock": missing, "Price": [None] * len(missing)}),
                                column_config={"Price": st.column_config.NumberColumn(format="₹%.2f")},
                                disabled=["Stock"], hide_index=True, key="manual_editor")
            if st.button("Use these prices"):
                for r in ed.dropna(subset=["Price"]).itertuples():
                    manual[r.Stock] = float(r.Price)
                st.rerun()
    if manual and st.button("Clear manual prices"):
        manual.clear()
        st.rerun()
    st.caption("Holdings are what the imported buys leave after FIFO-matching the sells.")

with tab_pnl:
    r = a.realized
    if r.empty:
        st.info("No sells matched to buys in this range.")
    else:
        by_stock = r.groupby("symbol", as_index=False)["pnl"].sum().sort_values("pnl")
        st.altair_chart(
            alt.Chart(by_stock).mark_bar().encode(
                x=alt.X("pnl:Q", title="Realized P&L (₹)"),
                y=alt.Y("symbol:N", sort="x", title=None),
                color=alt.condition("datum.pnl >= 0", alt.value(GREEN), alt.value(RED)),
                tooltip=["symbol", alt.Tooltip("pnl:Q", format=",.2f")]),
            width="stretch")
        rr = pd.DataFrame({"Stock": r["symbol"], "Bought at": r["buy_time"], "Sold at": r["sell_time"],
                           "Qty": r["quantity"], "Buy": r["buy_price"], "Sell": r["sell_price"],
                           "P&L": r["pnl"], "P&L %": r["pnl_pct"], "Days": r["holding_days"],
                           "Type": r["kind"]})
        st.dataframe(rr.style.format({**money(rr, ["Buy", "Sell", "P&L"]), "P&L %": "{:+.2f}%",
                                      "Qty": "{:g}", "Bought at": DT, "Sold at": DT})
                     .map(pnl_color, subset=["P&L", "P&L %"]),
                     hide_index=True, width="stretch")
    if not a.unmatched_sells.empty:
        st.subheader("Sells without a buy in this file")
        u = pd.DataFrame({"Stock": a.unmatched_sells["symbol"], "Sold at": a.unmatched_sells["sell_time"],
                          "Qty": a.unmatched_sells["quantity"], "Price": a.unmatched_sells["sell_price"],
                          "Value": a.unmatched_sells["sell_value"]})
        st.dataframe(u.style.format({**money(u, ["Price", "Value"]), "Qty": "{:g}", "Sold at": DT}),
                     hide_index=True, width="stretch")

with tab_stock:
    syms = sorted(fills["symbol"].unique())
    if st.session_state.get("detail_symbol") not in syms:
        st.session_state["detail_symbol"] = syms[0]
    sym = st.selectbox("Stock", syms, key="detail_symbol")
    render_stock_detail(sym)

with tab_trades:
    q1, q2, q3 = st.columns([3, 1, 1])
    query = q1.text_input("Search stock", placeholder="Type a symbol, e.g. HDFC",
                          key="trades_search").strip().upper()
    side = q2.selectbox("Side", ["All", "Buy", "Sell"], key="trades_side")
    with q3:
        st.write("")
        by_order = st.toggle("Group fills by order", value=True)
    if by_order:
        t = orders[["time", "symbol", "trade_type", "quantity", "avg_price", "value", "fills",
                    "exchange", "order_id"]].sort_values("time", ascending=False)
        fmt = {"time": DT, "quantity": "{:g}", "avg_price": INR, "value": INR}
    else:
        t = fills[["order_execution_time", "symbol", "trade_type", "quantity", "price", "value",
                   "exchange", "trade_id", "order_id"]].sort_values("order_execution_time",
                                                                     ascending=False)
        fmt = {"order_execution_time": DT, "quantity": "{:g}", "price": INR, "value": INR}
    if query:
        # an exact symbol match shows just that stock; otherwise match symbols containing the text
        exact = t["symbol"] == query
        t = t[exact] if exact.any() else t[t["symbol"].str.contains(query, regex=False)]
    if side != "All":
        t = t[t["trade_type"] == side.lower()]
    if t.empty:
        st.info(f"No trades found for “{query}”." if query else "No trades match.")
    else:
        st.caption(f"{len(t)} {'orders' if by_order else 'fills'}"
                   + (f" · {', '.join(sorted(t['symbol'].unique()))}" if query else ""))
        st.dataframe(t.style.format(fmt).map(side_color, subset=["trade_type"]),
                     hide_index=True, width="stretch", height=min(600, 38 + 35 * len(t)))
        st.download_button("Download as CSV", t.to_csv(index=False), "trades.csv", "text/csv")
