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

**Updates (Windows):** `start.bat` checks GitHub on every start and, when there is a new version,
downloads and installs it before starting. Your data and installed packages are kept. With no
internet it simply starts the version you have.

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
**All portfolios** compares them side by side against SPY and QQQ: a few automatic insights (biggest
winner and loser, the most widely held stock, any portfolio over 25% in one stock or 35% in one sector),
and a sortable table with each person's trend, value, gain vs cost in dollars, return vs the S&P 500 since
tracking began (shown after 20 trading days), today's move and last activity.

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

## Scenario impact

Every scenario, researched or typed ("oil falls 20%"), runs through one engine: each holding moves by its factor
betas times the factor moves (in shekels), plus sector and company-specific moves where given. 10,000 simulated
outcomes pick a branch by its probability, draw each move from its low/mid/high range with the factors' historical
correlations (sell-off weeks for crisis causes, normal weeks otherwise; you can switch), and add each holding's own
noise. The answer comes first ("Most likely: the family loses about ₪7,000 (−0.4%). Bad case (1 in 20): −₪41,000"),
then branch cards and a table per person. "See the analysis" shows the chain of moves (edit any of them and
everything downstream recalculates), the range of outcomes, and today's holdings replayed through the analogs, with a
warning when the model disagrees with them. "Model details" shows the exposures, regime and assumptions. A typed
scenario becomes a one-branch spec: the factors you didn't name take their usual move alongside the ones you did.

## Yesterday's earnings (morning brief)

A scheduled Claude task researches yesterday's reports and drops an Earnings Brief (`docs/earnings-brief.schema.json`,
example in `docs/examples/`) into the inbox folder. The app adds the numbers from its own prices: the reaction-day move
next to the last 8 reports (and how often the first-day direction held for 20 sessions), who in the family holds it and
their shekel P&L, and related holdings' expected moves. Each outlook goes into a prediction journal, scored after 5 and
20 sessions (needs the optional `days_direction`/`weeks_direction` fields), with accuracy per confidence level.

For the scheduled task: `GET http://localhost:8000/api/earnings/candidates?date=YYYY-MM-DD` lists up to 8 companies that
reported that day, family holdings first, then the largest S&P 500 names. Without a date it uses the last trading day.
The inbox is the `inbox` folder inside the app folder (the exact path is shown on the Researched and earnings tabs).

## My book (your view of the world)

What-if → My book: add saved scenarios, each with your chance that it happens within its horizon; the rest is
"nothing special" (normal conditions). The whole book runs as one simulation: the combined result for the family and
each person, a fragility score per portfolio (average loss in the worst 10% of outcomes), which scenarios hurt and help
each one, and every signpost from the book against the latest price (far / near / triggered). Every save is kept;
pick two versions to compare. New money can be weighted by the book.

## What each person could do, and where new money should go

Under any scenario result, "Show options for each person" ranks people by expected and bad-case loss in shekels,
names what drives each one, and offers up to three long-only options (trim, rebalance toward 15%, shift into
TLT/IEF/GLD, raise cash) plus doing nothing. Each is capped at half the portfolio and shows what it saves if the
scenario happens, what it costs if things go the other way, and its effect on the largest holding. "Draft message"
writes a short Hebrew note in your voice with only the numbers on screen; nothing is sent.

What-if → New money ranks your watchlist, everything the family holds, the sector funds and SPY/QQQ/TLT/GLD under
every saved scenario and normal conditions, robust choices first. Add to the watchlist from search, a stock's page,
or by typing "watch AVGO" in Ctrl+K.

## Researched scenarios (Scenario Specs)

Research a scenario outside the app (a Claude chat or a scheduled Claude task) and import the result: the app
never calls a paid API. The format is `docs/scenario-spec.schema.json` (example: `docs/examples/`). Import it two ways:

- **Paste:** What-if → Researched → Import researched scenario. The whole Claude message is fine; the app finds the JSON.
- **Inbox folder:** drop `*.json` into `inbox/` (or the folder set in Settings). Imported files move to `inbox/processed/`;
  files with errors move to `inbox/failed/` with a `.error.txt` explaining why.

Before saving, the app shows a report. Errors block saving: invalid format, unknown factor ids, branch
probabilities not adding up to 1, or low > mid > high out of order. Warnings don't: each past analog is replayed on our
own price data and any factor that actually moved against the branch is flagged, moves larger than anything seen over
that horizon, context older than 30 days, fewer than 2 analogs. The spec's own numbers are never changed.

## Stress tests, risk summary, memo, model accuracy

- **Risk → Risk summary:** historical 1-week and 1-month 95% loss for today's holdings (VaR) and the average loss beyond
  it (CVaR), each holding's share of risk vs its share of value, and the effective number of independent bets.
- **Risk → Stress tests:** today's holdings through 2008, the Covid crash, the 2022 rate shock, Q4 2018, the
  7 October week and the April 2025 tariffs, from real prices in shekels (holdings without prices then are estimated
  from their factor exposures and labeled). The reverse test finds the most likely month that would cost the family
  10% or 20% and the closest past stress.
- **Analyst memo** under every scenario: nine sections written only from the engine's numbers and the scenario's own
  research, in Hebrew or English (Settings), as a page, a PDF or a phone-sized image card.
- **Model accuracy** (Researched tab): every imported scenario's prediction is scored when its horizon ends, using only
  prices up to then. If too few outcomes land inside the 5–95% band, future ranges are widened automatically and the
  app says so.

## Exposures (Risk page)

What moves each portfolio, in shekels: market, size, value vs growth, momentum, quality, rates (TLT),
oil, the dollar, USD/ILS and gold, plus sector tilts. Each holding is regressed on weekly factor returns over
up to three years, shrunk toward its sector ETF (strongly when it has under a year of history). For a US stock
held in shekels the USD/ILS beta is about 1: when the dollar weakens 5% against the shekel, it loses about 5%
in shekels. Portfolio and family betas are value-weighted; each comes with a plain sentence ("If the S&P 500
falls 10%, Keren's portfolio falls about 11%"). Click a portfolio for its holdings, R² and residual volatility;
R² under 0.3 is marked low confidence. Recomputed once a day, and right away for a new symbol. Method: `backend/app/factors.py`.

## What-if scenarios

Type something that could happen, in Hebrew or English: "מלחמה עם איראן", "הנפט יורד 20%",
"תשואות האג״ח בארה״ב עולות", "Apple misses earnings". The page turns it into a few market moves you
can edit, then shows for every portfolio and the whole family:

- the expected impact in % and money, with a range (about 2 in 3 outcomes), using normal and sell-off correlations
- what else rises and falls, and each holding's move with the reason ("oil +1.6%, defense −0.3%")
- today's holdings replayed through past events like it (Iran 2024–2025, October 7, COVID, 2022 rate hikes, SVB, tariffs…)
- how big the same story would need to be to cost 10%, and how often history has seen weeks that unusual
- protection ideas, long-only by default (trim, rebalance, add bonds or gold, raise cash), each capped at half
  the portfolio and sized for the worst past event used; shorts and inverse funds only if you turn them on in Settings
- "How reliable is this?": the model rebuilt before each past event and scored against what really happened

Sentences are read by free local rules ("oil falls 20%", "war with Iran"). Free-text research is coming later.
The headline shows the result in normal markets and in a sell-off; normal is the default, and you choose. The first run loads ten years of prices for ~26
market ETFs; the app starts fetching them in the background when it starts. Method and sources:
[docs/SCENARIOS.md](docs/SCENARIOS.md). Swap-a-holding and build-a-portfolio are still on the same page.

## Company logos

Logos are fetched once by the app from free public sources that need no key (Parqet, Financial Modeling
Prep's image endpoint, or the company website's icon), saved in `data/logos/`, and served from there.
A symbol without a logo shows its letter. To fix a wrong or missing logo, upload your own in Settings.

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
