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
