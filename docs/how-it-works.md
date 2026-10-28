# How it works

## Input format

The app expects Zerodha Console's equity tradebook. Each row is one **fill** (an execution):

| Column | Meaning |
| --- | --- |
| `symbol`, `isin` | Stock |
| `trade_date`, `order_execution_time` | When the fill happened |
| `exchange` | NSE or BSE |
| `trade_type` | `buy` or `sell` |
| `quantity`, `price` | Fill size and price |
| `trade_id` | Unique id of the fill |
| `order_id` | The order it belongs to; one order can have several fills |

Excel exports with a few lines of client details above the table are supported: the parser
looks for the header row. Column names like "Trade Date" or "trade_date" are both accepted.

When several files are imported, rows with the same exchange, symbol, trade id and order id
are treated as duplicates.

## Fills and orders

"All trades" and the stock details group fills by `order_id`. An order's price is the
quantity-weighted average of its fills.

## FIFO matching

Fills are processed in time order, per stock:

- A **buy** adds a lot (quantity, price, time) to the stock's queue of open lots.
- A **sell** takes shares from the oldest open lots first. Each match produces a realized
  trade: `P&L = quantity × (sell price − buy price)`.
- If a sell finds no open lots, it waits for the rest of that day: a buy later the same day
  covers it as an **intraday** trade. Otherwise it's recorded as a **sell without a buy**
  (shares bought before the data starts) and is not included in P&L.

Matches where the buy and sell happen on the same day are labelled *intraday*; the rest are
*delivery*.

What's left in the queue at the end are your **holdings**, with an average cost based on the
remaining lots.

> If you held shares from before your data starts **and** bought more inside it, FIFO will
> match your sells against the newer buys, because the older ones aren't in the data. Import
> older tradebooks for accurate P&L.

## Holding periods

A holding period starts with a buy when you hold none of that stock, and ends when a sell
brings the position back to zero. Its net P&L is the sum of the matches inside it. A period
that hasn't ended is shown as "still holding".

## Time periods

The "By time period" tab adds up buy value, sell value and order counts by trade date, and
books realized P&L in the period of the **sell**.

## Current prices

`tradebook/prices.py` fetches the last close (or the live, about 15-minute delayed price during
market hours) for each holding from Yahoo Finance using `yfinance`. A stock is looked up as
`SYMBOL.NS` or `SYMBOL.BO`, starting with the exchange you last traded it on. Prices are
cached for 5 minutes.

From those, holdings get: current value, unrealized P&L (`current value − invested`) and
today's change (`quantity × (price − previous close)`).

Price sources follow a small interface (`fetch(symbols) -> DataFrame`), so a Kite Connect
provider can be added later without changing the rest of the app.

## Storage

Imported fills are kept in `data/trades.db` (SQLite, table `fills`). Nothing leaves your
computer except the price lookups to Yahoo Finance, which send only stock symbols.
