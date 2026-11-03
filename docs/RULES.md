# Rules

Every step of work on Benchmark follows these rules. If a request conflicts with one, say so before building.

## Process
- Diagnose → short plan → build → tests → verify in the browser → report what was checked.
- One step at a time. Never start the next step on your own.

## Money
- Stock performance only. Every return is measured in the security's own trading currency (US in USD, `.TA` in ILS).
- Portfolio and family returns = value-weighted average of local-currency returns.
- No FX effect in any return, benchmark, risk figure or scenario.
- Amounts are shown in the currency chosen in Settings, converted at today's rate for display only.
- Never mix currencies on one screen.

## AI
- OpenRouter only, within the monthly budget set in Settings.
- Nothing sent outside the app contains person names, portfolio names or amounts. Tickers, weights and exposures only.
  Every outgoing request passes the privacy check (`backend/app/privacy.py`).

## Numbers never lie
- Missing data is labeled as missing, never filled or guessed.
- Source and as-of date on hover.
- One function per metric; the same value on every screen.

## Writes
- Nothing is recorded without the user's confirmation.
- Every write goes to the audit log and is undoable.

## Audience and language
- Single user: the owner is the only person who reads this app. Nothing is designed for showing to family members
  (no meeting mode, no per-person periodic reports in plans).
- Everything is in English, left to right: UI, computed answer sentences, AI memos (memo language setting kept,
  default English), Telegram replies and digests, Ask answers.
- Hebrew input keeps working: omnibox commands ("קרן קנתה 5 AVGO"), nicknames, Ask questions. Any Hebrew text
  shown (names) uses `dir="auto"`.
- All UI strings live in one `en.json`; no hardcoded text in components.
- Keep the privacy blur (H) for using the app in public.

## Design
- Follow `PRODUCT.md` and `DESIGN.md`.
- Answer first, then layered detail.
- Dark theme is the default and gets full design attention; light must pass contrast and look clean.
- CSS stays RTL-ready (logical properties), but there is no Hebrew UI.
