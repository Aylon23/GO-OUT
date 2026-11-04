# UI to build

The backend for these is done. The frontend is being redesigned separately, so screens are specified here
instead of built. Every amount follows the display currency from Settings; every percent is stock performance
only (see docs/RULES.md). Endpoints are under `/api`.

## 1. AI debate: "preparing" phase

`POST /debates` now returns at once with `phase: "preparing"` while the app builds the context pack in the
background (the first run fetches a lot of price history and can take a few minutes). Then the phase moves
through `round1`, `round2`, `judge`, `done` (or `failed` with `note`).

- Add `"preparing"` to the `DebateRow.phase` type.
- Progress text for it: "Preparing the data: market snapshot, past events and your holdings. The first time
  this can take a few minutes."
- The submit button's busy label: "Reading the question…" (it is now only the quick read of horizon and cause).

## 2. Backups

Already in the current UI: Settings → Data (last backup, folders, Back up now, list with Restore) and a
warning on the home pages. Endpoints for the redesign:

| Endpoint | What |
|---|---|
| `GET /backups` | `last_backup`, `warn`, `days_since`, `locations[]`, `backups[]` (stem, at, safety, has_excel, bytes, in_extra), `extra_dir` |
| `GET /backups/last` | `last_backup`, `days_since`, `no_backup_3_days` |
| `POST /backups` | back up now; returns the status plus `made` (stem, rows, folders, errors) |
| `PUT /backups/settings` | `{extra_dir}`: the second folder ("" removes it); 400 with a message if unusable |
| `POST /backups/restore` | `{stem, confirm: true}`; a safety backup is made first; returns `{restored, safety}` |
| `POST /backups/undo-restore` | restores the safety copy of the last restore |

Home queue: while backups are 3+ days old there is one open action item with `source: "backup"`
(`GET /actions?source=backup`); it closes itself after the next backup. Restore needs an inline confirm
("Replace all current data with this backup? A safety copy is made first.") and then offers "Undo restore".

## 3. History import (per person)

Endpoints and rules: docs/import-format.md. Entry point: the person's Trades page, "Import history (CSV)".

**Screen A: Import.** Pick the person (defaults to the current portfolio), drop or choose a CSV, link to the
format. On file: `POST /history/validate`, then show
- one answer line first: "142 rows: 136 new, 4 already imported, 2 with errors";
- a table of rows with errors and warnings only (line or source_line, date, type, symbol, quantity, price, the
  message), errors first; "Show all rows" expands to every row with its status;
- primary button "Import 136 new rows" (disabled when there are none). Rows with errors are never imported, so
  say "2 rows with errors will be skipped" next to it.

**Screen B: Reconciliation diff** (after import, when the person has opening positions; also reachable from
`GET /history/batches?portfolio=` for a staged batch). `GET /history/batches/{id}/reconcile`.
- Answer first: "12 match, 3 differ, 2 only in history, 1 only in your opening positions."
- Table per symbol: symbol, category, history quantity and average cost, opening quantity and average cost,
  and a two-way choice "Use history / Keep opening position", preset to `default`. Sort: differences first,
  then only-in-one-side, then matches; "Closed in history" rows collapsed under a line.
- Explain once above the table: "Each stock uses one source, never both. Replaced opening positions are kept in
  the archive, and you can undo the whole import."
- Primary button "Apply" → `POST /history/batches/{id}/apply` with `{decisions}`. Then show the result: rows
  added, opening positions archived, and before/after (first trade, value, TWR, XIRR); a toast with "Undo"
  (`POST /history/batches/{id}/undo`).

**Screen C: Suggested recommendations review.** `GET /history/suggested-calls`. A list: direction and symbol
("Buy MSFT"), first date, close that day, the people (avatars), the note. Per row: "Add to my calls"
(`/approve`) and "Not a call" (`/dismiss`). Empty state: "No suggested calls. They appear when two or more
people trade the same stock within 10 trading days." Surface the count on the Calls/Scores page and as a Home
queue item.

**Also:** transaction lists show FEE as a type ("Fee", an amount, no quantity), and archived rows are not shown.
