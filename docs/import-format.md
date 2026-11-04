# Transaction history import format

One CSV file per portfolio (person). The first row names the columns; order doesn't matter. Comma, semicolon
or tab separated. UTF-8 (Excel's "CSV UTF-8" works).

| Column | Required | Meaning |
|---|---|---|
| `date` | yes | Trade or cash date: `2024-03-15` (also `15/03/2024`, `15.03.2024`). |
| `type` | yes | `BUY`, `SELL`, `DIVIDEND`, `SPLIT`, `FEE`, `DEPOSIT` or `WITHDRAWAL`. |
| `symbol` | for BUY, SELL, DIVIDEND, SPLIT | Yahoo Finance symbol: `AAPL`, `BRK-B`, Tel Aviv listings end in `.TA` (`TEVA.TA`). Optional for FEE; empty for DEPOSIT and WITHDRAWAL. |
| `quantity` | BUY, SELL, SPLIT | Shares as traded (before any later split). For SPLIT: the ratio (`4` for a 4-for-1 split). |
| `price` | yes | BUY/SELL: price per share. DIVIDEND, FEE, DEPOSIT, WITHDRAWAL: the cash amount. Tel Aviv prices in shekels, not agorot. |
| `currency` | no | `USD` or `ILS`. Default: ILS for `.TA`, USD otherwise. |
| `fees` | no | Commission for the row, in its currency. On a DIVIDEND: tax withheld. |
| `note` | no | Free text. |
| `source_line` | no | Your reference (bank row number or id), shown in error messages. |

Example:

```
date,type,symbol,quantity,price,currency,fees,note,source_line
2021-03-01,DEPOSIT,,0,10000,USD,0,Transfer in,1
2021-03-02,BUY,AAPL,20,121.40,USD,5,,2
2021-09-15,DIVIDEND,AAPL,0,4.40,USD,0.66,Tax withheld 15%,3
2022-01-10,SELL,AAPL,5,172.10,USD,5,,4
2022-06-30,FEE,,0,12.50,USD,0,Custody fee,5
2022-07-15,BUY,TEVA.TA,100,29.85,ILS,10,,6
```

## Rules

- **Checked per row before anything is saved:** unknown ticker, selling more than held on that date (rows of the
  file plus trades entered by hand), a date before the symbol's first trading day, a future date, missing or
  impossible values, a duplicate. Rows with errors are never imported. A trade price more than 25% from that
  day's close, or a Tel Aviv price that looks like agorot, is a warning.
- **Duplicates:** each row is identified by its content (date, type, symbol, quantity, price, currency, fees) and
  its occurrence among identical rows of the file, so two real identical trades on one day are both kept, while
  importing the same file again, or an overlapping one, adds nothing. A row matching a trade already entered by
  hand (same date, symbol, quantity and price) is a duplicate too.
- **SPLIT** rows are checked against Yahoo's split history and kept as information: Yahoo's prices are already
  split-adjusted and the app applies splits to quantities itself, so applying them again would double the split.
- **FEE** lowers the value and the return; it is not counted as money taken out (unlike WITHDRAWAL).
- **Opening positions** (imported from a holdings list: one BUY per stock on the import day, at average cost)
  are never counted together with the history. The import compares them per symbol:
  - *Match*: same quantity and average cost (within 1%).
  - *Quantity differs* / *Avg cost differs*.
  - *Only in history*: held according to the history, not in the opening positions.
  - *Only in opening positions*: probably bought before the history starts.
  - *Closed in history*: bought and sold within the history; nothing to reconcile.

  On approval each symbol uses one source: the history (default) or the opening position (default for "Only in
  opening positions"). Replaced opening positions and realized-profit lines are archived, never deleted, and the
  whole import can be undone.
- **After applying**, returns (TWR, XIRR), the SPY and QQQ shadow portfolios and risk figures are rebuilt from
  the first real trade date.
- **Suggested calls:** the same symbol bought (or sold) by two or more portfolios within 10 trading days becomes a
  suggested recommendation (direction, first date, people, close that day). It isn't counted as one of your calls
  until you approve it.

## Endpoints

| Endpoint | What |
|---|---|
| `POST /api/history/validate` | `{portfolio, text}` → rows with `status` (new, duplicate, error), `errors`, `warnings`; counts. Saves nothing. |
| `POST /api/history/preview` | Same, plus `diff` (per-symbol categories and defaults), `first_date`, `needs_reconciliation`. Saves nothing. |
| `POST /api/history/import` | `{portfolio, text, filename}` → a batch. Applied at once when the portfolio has no opening positions; otherwise `staged` with the diff. |
| `GET /api/history/batches?portfolio=` | Import batches. |
| `GET /api/history/batches/{id}/reconcile` | The diff for a staged batch. |
| `POST /api/history/batches/{id}/apply` | `{decisions: {SYMBOL: "history" or "opening"}}` → added, archived, kept out, before/after summary (first trade, value, TWR, XIRR), new suggested calls. |
| `POST /api/history/batches/{id}/undo` | Takes the import out and restores what it replaced. |
| `GET /api/history/suggested-calls` | Suggested calls (`?refresh=true` looks for new clusters first). |
| `POST /api/history/suggested-calls/{id}/approve` / `dismiss` | Approve (links the cluster's trades to the call) or dismiss. |
