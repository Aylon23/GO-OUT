# Benchmark

A local, free portfolio tracker that answers one question: **did my stock picking beat just buying the index?**

- Your performance (time-weighted, so deposits don't distort it) next to SPY and QQQ total return.
- A "shadow portfolio": your exact deposits, on the same dates, put into SPY or QQQ instead. The difference in money and percent.
- Holdings, risk stats, a monthly heatmap, a what-if simulator, and Yahoo-style symbol search with a ⌘K palette.

Single user, runs on localhost, no account, no cloud, no paid APIs. Prices come from Yahoo Finance through
[`yfinance`](https://github.com/ranaroussi/yfinance) (free, end-of-day).

## Run it

Requirements: Python 3.11+ and Node 18+.

```bash
./start.sh
```

Then open http://localhost:5173. The first run creates `.venv`, installs Python and npm packages, and starts the
API (port 8000) and the web app (port 5173) together. Stop with Ctrl+C.

Other modes:

```bash
./start.sh --prod   # build the frontend and serve everything from http://localhost:8000
```

```bash
./start.sh --test   # run the backend test suite
```

Your data lives in `data/portfolio.db` (SQLite). Delete that file to start over.

New here? Click **Load demo portfolio** on the empty overview page. It loads AAPL, MSFT, NVDA, VOO and TEVA.TA
bought on different dates, with a few shekel deposits, priced from real closes on those days.

### Offline development

`PORTFOLIO_PROVIDER=synthetic` swaps Yahoo for deterministic random-walk prices (not real data). Useful for UI work
without network access:

```bash
PORTFOLIO_PROVIDER=synthetic PORTFOLIO_DB=/tmp/synthetic.db ./start.sh
```

## Several portfolios

Use the switcher next to the app name to pick a portfolio, open **All portfolios**, or add one.
Every page (Overview, Holdings, Risk, Compare, Transactions, What-if) shows the selected portfolio.
**All portfolios** compares them side by side against SPY and QQQ.

### Importing holdings from Excel

**Import holdings** (on Transactions or All portfolios) takes an `.xlsx` or `.csv` with a header row:

| ticker | quantity | avg_price | currency |
|---|---|---|---|
| QQQ | 60 | 561.30 | USD |
| TEVA.TA | 300 | 45.10 | ILS |

- Hebrew headers work too (סימול, כמות, שער ממוצע, מטבע), and so do `amount` / `cost rate`.
- Excel "Stocks" cells (which show as `#VALUE!` outside Excel) are read from the file directly.
- **Several portfolios in one sheet:** put each portfolio's table under a row with just its name.
  Each table goes into the portfolio with that name (created if missing). Totals rows and lines
  without a quantity are skipped.
- Re-importing an updated file with **Replace what these portfolios hold now** swaps the holdings
  instead of adding them twice.

There are no purchase dates in a holdings list, so:
- **Gain vs cost** uses your average price (real money, but no timeline).
- **Performance vs the S&P 500** is real and starts on the import day: the portfolio and SPY/QQQ are
  measured over exactly the same days. To compare earlier years, import the transaction history
  (date, ticker, buy/sell, quantity, price) instead; then every number covers the full period.

## Compare: beta and P/E

The **Compare** page puts portfolios next to any stocks or indices you add (SPY, QQQ and XLK, the
tech sector, by default):
- **Beta vs SPY** from daily returns over the range, plus a rolling 6-month beta chart.
- **P/E and forward P/E.** For a portfolio this is total value divided by total earnings (a weighted
  harmonic mean); holdings without earnings are left out and listed. Yahoo only offers today's P/E
  for free, so P/E is a snapshot, not a history. Indices use the ETF that tracks them.
- Dividend yield, weighted.

## Recommendations (Calls)

Every buy or sell you add with Ctrl/⌘ K or from Telegram can be saved as a **recommendation**, linked
to every portfolio that followed it. You can also log one by hand on the **Calls** page.

- A buy call is measured from the close on its date until today, or until you call "sell" on the
  same ticker. **Edge** = its total return minus the S&P 500 (SPY) over exactly the same days.
- A sell call is measured after the date: it worked if the stock then did worse than the S&P 500.
- The scorecard shows how many calls beat the S&P, the average edge, best, worst and holding time.

## Realized gains

Sales you record are counted with the average-cost method. Lines like `tsla | -1565` under a
portfolio in your spreadsheet (a ticker and one amount, no quantity) are imported as realized gains.

## Reports

**Report** on a portfolio (or on All portfolios) gives a short Hebrew summary for the week, month or
year, ready to copy into WhatsApp. The Telegram bot can send the all-portfolios summary every Sunday.

## Telegram bot

Settings → Telegram: create a bot with @BotFather, paste its token, then send `/start CODE` (the code
shown in Settings) to your bot. Only that chat can use it. Then, from your phone:

- `קרן קנתה 5 AVGO` → a draft with buttons: add and save as a recommendation, add only, or cancel
- `/report`, `/report קרן`, `/month`, `/calls`, `/portfolios`, `/help`

The bot asks Telegram for new messages, so it works from a home computer with no setup, but only
while the app is running. To have it always on, run the app on a small always-on machine or server.

## Command box

Press **Ctrl/⌘ K** and type a trade in Hebrew or English. The app fills in the rest and asks you to
confirm before saving:

- `קניתי 5 מניות של QQQ` → buy 5 QQQ at today's close, in the current portfolio
- `אבא קנה SPY ב-1000 דולר` → buy $1,000 of SPY in the portfolio named אבא
- `Keren sold 3 AAPL at 190 yesterday`
- `הפקדתי 5000 שקל לתיק של Shir`

It runs locally with simple rules (no AI service). Name the portfolio as it appears in the app, or by a
nickname set in Settings (e.g. Keren = קרן). Several at once work too:

- `קרן, שיר ואבא קנו 3 NVDA` → one recommendation, three trades
- `כולם מכרו TSLA` → sells the whole position in every portfolio that holds it

## How the numbers work

| Rule | What the app does |
|---|---|
| Tel Aviv prices | Yahoo quotes `.TA` listings in agorot. Prices are divided by 100 on the way in (indices like `^TA125.TA` excluded). |
| Currency | Every amount is converted to your base currency (₪ by default) with **that day's** USD/ILS rate (`ILS=X`), never today's. |
| Splits | Quantities are multiplied by every split after the trade date, so a 10-for-1 split never breaks history. Prices you see for old dates are "as traded". |
| Dividends | Logged DIVIDEND transactions count as cash. For holdings with no logged dividends, cash dividends from the price feed are added automatically (toggle in Settings). Benchmarks use total return (`Adj Close`). |
| Calendar | One daily calendar across exchanges (TASE trades Sunday, US does not). Every series is forward-filled onto it. |
| Performance | Time-weighted return, chained daily: `r = (V_today − flow_today − V_yesterday) / V_yesterday`. Deposits and withdrawals don't move it. |
| Your return | XIRR: the annual rate that matches your actual cash flows and today's value. |
| Shadow portfolio | Each deposit (and the range's starting value) buys the benchmark at that day's total-return price; withdrawals sell it. |
| No deposits logged? | Buys that need more cash than you have are treated as new money deposited that day. |
| Cache | Each ticker's full history is fetched once. Later visits fetch only the days since the last cached day. |

All of this is unit-tested with hand-computed fixtures in `backend/tests/` (agorot, FX, splits, TWR, XIRR, max
drawdown, beta, shadow portfolios, search ranking, validation, CSV).

## CSV format

Import and export use the same columns, with a header row:

```csv
date,type,ticker,quantity,price,currency,fees,note
2023-04-03,BUY,AAPL,12.5,187.20,USD,1.50,First buy
2023-06-01,BUY,TEVA.TA,100,32.10,ILS,0,
2024-02-15,DIVIDEND,AAPL,0,2.85,USD,0,After tax
2024-03-01,SELL,AAPL,5,180.00,USD,1.50,
2023-04-01,DEPOSIT,,0,10000,ILS,0,Monthly savings
2024-05-01,WITHDRAWAL,,0,2000,ILS,0,
```

- `date`: `YYYY-MM-DD`.
- `type`: `BUY`, `SELL`, `DIVIDEND`, `DEPOSIT` or `WITHDRAWAL`.
- `ticker`: Yahoo symbol (`AAPL`, `VOO`, `TEVA.TA`). Empty for deposits and withdrawals.
- `quantity`: shares, as traded on that date (before any later split). Fractions are fine. `0` for cash rows.
- `price`: per-share price for BUY/SELL, in shekels (not agorot) for `.TA`. For DIVIDEND, DEPOSIT and WITHDRAWAL it is the total cash amount.
- `currency`: `USD` or `ILS`. If empty, taken from the symbol.
- `fees`, `note`: optional.

Import shows a preview with an error for each bad row (unknown ticker, date before the first trading day, selling
more than you held). Only valid rows are imported.

## API

FastAPI serves interactive docs at http://localhost:8000/docs. Main endpoints:

- `GET /api/search?q=` symbol search (Yahoo, ranked, cached 24h, local fallback)
- `GET /api/quote/{symbol}`, `GET /api/quotes?symbols=` last price and day change (cached 15 min)
- `GET /api/price/{symbol}?date=YYYY-MM-DD` close on that date or the session before
- `GET|POST|PUT|DELETE /api/transactions`, `POST /api/transactions/import`, `GET /api/transactions/export`
- `GET /api/portfolio?range=1Y` headline numbers, chart series, shadow portfolios
- `GET /api/holdings?range=`, `GET /api/holdings/{ticker}`, `GET /api/risk?range=`
- `POST /api/whatif/swap`, `POST /api/whatif/custom`
- `GET|PUT /api/settings`, `POST /api/demo`

Ranges: `1M 3M YTD 1Y 3Y 5Y MAX`, or `range=CUSTOM&start=YYYY-MM-DD&end=YYYY-MM-DD`.

## Project layout

```
backend/app/
  market/provider.py   the only module that talks to Yahoo
  market/cache.py      price / FX / split cache, agorot fix, missing-range fetches
  market/search.py     search ranking, filters, caches, quotes
  engine/series.py     daily portfolio series (positions, cash, FX, flows, TWR returns)
  engine/returns.py    ranges, TWR, XIRR, shadow portfolios
  engine/risk.py       CAGR, volatility, drawdown, Sharpe, beta, yearly and monthly returns
  services.py          builds API payloads, what-if simulations
frontend/src/
  components/SymbolSearch.tsx   the one search component used everywhere
  components/chart/SeriesChart.tsx
  pages/                        Overview, Holdings, Risk, Transactions, What-if, Settings
  styles/tokens.css             design tokens (see DESIGN.md)
PRODUCT.md  DESIGN.md           product and design context
```

## Limits

End-of-day prices only. USD and ILS listings only (US exchanges and Tel Aviv). No broker sync, tax reports,
options or crypto. Yahoo's free endpoints are unofficial and occasionally rate-limit; the app keeps serving cached
data and retries on the next visit.
