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
