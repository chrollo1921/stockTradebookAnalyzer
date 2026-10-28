# Stock Tradebook Analyzer

A local web app that reads your **Zerodha equity tradebook** (CSV or Excel) and shows what you
bought and sold: per-stock summaries, holding periods, realized and unrealized P&L, and
P&L by day, week or month.

Everything runs on your own computer. Your trades are stored in a local SQLite file and are
never uploaded anywhere.

## Features

- **Import** one or more tradebook exports (CSV or Excel). Overlapping date ranges are merged
  and duplicate trades skipped, so your history builds up over time.
- **Per-stock summary**: quantity bought and sold, average buy and sell price, realized P&L,
  what you still hold. Click a row to open that stock's details.
- **Stock details**: a price chart of every buy and sell, each holding period (first buy until
  the position is back to zero) with days held and net P&L, every buy matched to its sell
  with exact times, and every order and fill.
- **By time period**: buys, sells, net P&L and cumulative P&L by day, week or month, with the
  orders placed in any period.
- **Holdings with current prices**: current price, today's change, current value and
  unrealized P&L (prices from Yahoo Finance, free, about 15 minutes delayed).
  Prices for stocks Yahoo doesn't list can be entered by hand.
- **Realized P&L**: per stock and per matched trade, plus a list of sells that have no buy
  in your data.
- **All trades**: searchable list of orders (or raw fills), filter by stock and buy/sell,
  download as CSV.

## Setup

Requires Python 3.10 or newer.

### With conda (environment inside the project folder)

```bash
git clone https://github.com/chrollo1921/stockTradebookAnalyzer.git
cd stockTradebookAnalyzer
conda create --prefix ./env python=3.12 -y
conda activate ./env
pip install -r requirements.txt
```

### With venv

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```bash
streamlit run app.py
```

This opens http://localhost:8501.

1. Download your tradebook from **Zerodha Console → Reports → Tradebook**, segment
   **Equity**, for the date range you want (CSV or Excel).
2. In the app's sidebar, choose the file(s) and click **Import**.
3. Use the sidebar filters to narrow dates or stocks.

To try the app without your own data, import `sample/sample-tradebook.csv` (made-up trades).

Your imported trades are kept in `data/trades.db`. Use **Clear stored history** in the
sidebar to delete them. The `data/` folder is git-ignored.

### Run the tests

```bash
python -m pytest
```

## How the numbers are calculated

See [docs/how-it-works.md](docs/how-it-works.md) for details. In short:

- Sells are matched to buys **first in, first out**, per stock (NSE and BSE combined).
- A sell with no earlier buy in your data (shares bought before the exported range) is shown
  separately and **left out of P&L**. Import older tradebooks to fill the gap.
- P&L is **gross**. The tradebook doesn't include brokerage, STT or other charges.

## Project structure

```
app.py                    Streamlit UI
tradebook/
  parser.py               Load and normalise CSV/Excel exports; group fills into orders
  analytics.py            FIFO matching: summary, holdings, realized P&L, holding periods,
                          period summary
  prices.py               Current prices (Yahoo Finance provider) and holdings valuation
  store.py                Local SQLite history of trades
tests/                    pytest suite
sample/                   Made-up tradebook for trying the app
scripts/make_sample.py    Regenerates the sample file
docs/                     Documentation
```

## Roadmap

1. **Fetch trades automatically** with [Kite Connect](https://kite.trade/): a scheduled job
   that calls `kite.trades()` and saves into the same store (`TradeStore.add`).
2. **Real-time prices** with a Kite Connect price provider (`kite.ltp()`), alongside the
   current Yahoo Finance provider.
3. **Buy-timing signals and alerts**: use historical prices to suggest good times to buy and
   send alerts (for example Telegram or email).

## Disclaimer

This is a personal tool for reviewing your own trades, not financial advice. Check figures
against Zerodha Console before relying on them for tax or investment decisions.
