# Benchmark: QA and analyst review

Reviewer: senior buy-side analyst + QA. Date: 4 Nov 2026. Build reviewed: localhost:5173 after the "Ink & Signal" design pass.
Bar: **TradingView and Koyfin.** Every item says what is wrong, why it matters to someone who manages money, the fix, and how to verify it.

Priorities: **P0** = wrong or misleading numbers (trust). **P1** = missing what a TradingView/Koyfin user expects on day one. **P2** = UX and density. **P3** = polish.
Constraints that stay: local only, free data (yfinance, SEC EDGAR, RSS), AI budget per Settings, stock performance only (no FX effects), single user, English UI.

---

## P0. Trust: numbers and labels

**P0-1. Headlines draw conclusions from 3 trading days.**
Seen: "5 of 6 are ahead of the S&P 500 since 30 Oct 2026", "Up 0.3% … 0.2 points ahead of SPY". Three sessions is noise; a professional tool never headlines it.
Fix: below 20 trading days, the headline leads with value and gain vs average cost and says "Too early to compare with SPY (3 of 20 trading days)". Comparisons switch on automatically at 20.
Verify: a portfolio tracked for 3 days shows no "ahead/behind" sentence anywhere (headline, table, reports, Telegram).

**P0-2. Mixed currencies in one row.**
Holdings: Avg cost and Price in $, Unrealized in $, Value and Today in ₪, all in the same row. A reader cannot add across a row.
Fix: one display currency per screen (from Settings), applied to every amount column. Price and avg cost stay in the security's own currency but carry the symbol and a column header "(local)". Unrealized follows the display currency.
Verify: on Holdings, every amount column except Price/Avg cost uses the same symbol; a test asserts it.

**P0-3. Display currency default.**
Agreed default was USD; the app shows ₪ everywhere. Confirm the setting and make USD the default for new installs.

**P0-4. "Today" is not today.**
On Wed 4 Nov 13:40 Israel time the dashboard says "Today · 3 Nov 2026". That is the last completed session.
Fix: label "Last session · Tue 3 Nov (close)" and add a market status chip in the header like TradingView: Pre-market / Open / After hours / Closed, with the as-of time of the last price. If pre-market quotes are available from the feed, show them separately and never mix them into returns.

**P0-5. Two different time frames side by side without labels.**
Holdings row for QQQ: Unrealized +22.1% (since bought) next to Return −0.5% (selected range, here since 30 Oct). Looks contradictory.
Fix: headers carry their period: "Gain since bought", "Return · Max (since 30 Oct)". Range change updates the header text.

**P0-6. XIRR shows "–" with no reason.**
Fix: "XIRR needs dated cash flows. Import transaction history." with a link to the importer. Same rule for every blank metric: blank always says why.

**P0-7. Internal labels leak into results.**
Scenario result shows a branch named "other" at 100%. Replace with the scenario's own name or "Single path". Search the UI for any raw enum or key text.

**P0-8. Scenario result formatting is inconsistent.**
"Most likely −7.3% −₪130,233" vs "Bad case −₪156,154 −8.7%": order flips between columns. "Good case −₪103,968" is a loss labeled good. "Chance of loss 100%" implies false precision.
Fix: always % first then amount; rename to "Best of 20 / Typical / Worst of 20"; show probabilities as "> 99%" / "< 1%" at the extremes.

**P0-9. Forward P/E above trailing P/E for most portfolios.**
Keren 21.6 → forward 22.5, Mouwes 21.8 → 23.1, Dad 21.8 → 22.5. For growth-tilted books this implies falling earnings, which is unusual. Likely a mix of holdings with and without forward estimates.
Fix: compute trailing and forward on the same set of holdings (intersection), show coverage for each ("forward covers 91% of value"), and flag any holding whose forward P/E is missing or stale.
Verify: a fixture where all holdings have both values gives forward < trailing when estimates grow.

**P0-10. Beta without context.**
Beta matches an independent recomputation from the app's own prices (XLK 1.81, QQQ 1.47 vs SPY, 252 daily returns), so the math is right. What's missing is context a professional expects: window, R² and correlation, and stability.
Fix: show "β 1.47 · R² 0.86 · 1Y daily" and on hover the 3M/1Y/3Y values. Flag low-R² betas as unreliable.

**P0-11. Zero rows in sector breakdowns.**
"Basic Materials 0%, Real Estate 0%" listed. Hide anything that rounds to 0; group under 1% as "Other".

**P0-12. Slow, silent scenario parsing.**
"Nasdaq falls 10%" took ~25 seconds on "Reading" with no progress, for a sentence the local rules handle. Also "Free-text research coming soon" shows up as a note.
Fix: local-rule parses must return in < 300 ms; anything slower shows steps ("Reading the sentence · Finding past weeks · Simulating 10,000 outcomes"). Remove "coming soon" text from the product.

**P0-13. Page load time.**
Overview and All portfolios show skeletons for 5–10 seconds on every visit. TradingView and Koyfin feel instant.
Fix: cache the last good response per endpoint and render it immediately (stale-while-revalidate) with an "Updated 13:40" stamp; refresh in the background. Budget: cached first paint < 500 ms, fresh data < 2 s.

**P0-14. Agreed scope not applied.**
All portfolios still shows the per-person Reports section, which was dropped (single user). Move reports out of the main page (Settings or the Scores tab), or remove.

---

## P1. What TradingView / Koyfin users expect

**P1-1. Real charts (TradingView Lightweight Charts, free, Apache-2.0).**
Replace the Recharts price charts on stock views with Lightweight Charts: candles or line toggle, volume pane, right-side price scale, last-price label on the axis, crosshair with axis labels for date and price, log/linear toggle, drag to pan, scroll to zoom, double-click to reset, compare overlay (% mode) for SPY/QQQ/sector ETF, and markers for your buys/sells and earnings dates. Keep the dashed SPY benchmark line from DESIGN.md. Include the required attribution link.

**P1-2. Security page (Koyfin "Snapshot").**
One page per ticker, reachable from every ticker in the app and from Ctrl+K:
- Header: price, change, market status, 52-week range bar, market cap, sector.
- Chart (P1-1) with 1D/5D/1M/3M/6M/YTD/1Y/5Y/Max.
- Key stats grid: P/E, forward P/E, EV/Sales, dividend yield, beta, short interest if available, next earnings date.
- Valuation history: P/E over 5 years with median and ±1σ bands.
- Earnings: last 8 quarters EPS and revenue actual vs estimate, with the stock's reaction (day 1/5).
- Who in the family holds it, cost, gain, weight; recommendations made on it; council verdict (when built).
- News (free RSS), latest filings (EDGAR) with links.

**P1-3. Market overview page (Koyfin "Market Snapshot").**
Indices (S&P 500, Nasdaq 100, Russell 2000, TA-125), sectors (11 SPDR ETFs), rates (2Y, 10Y), commodities (Brent, gold), USD/ILS, VIX, Bitcoin. Columns: last, 1D, 1W, 1M, YTD, 1Y, sparkline. Sortable, color only on signed values.

**P1-4. Holdings heatmap (TradingView-style treemap).**
Family or person: rectangles sized by weight, grouped by sector, colored by the selected period's return (diverging, capped), label = ticker + %. Click opens the security page.

**P1-5. Watchlist panel.**
Persistent right-side panel (collapsible, like TradingView): symbols with last, change %, sparkline; add from Ctrl+K; groups ("Ideas", "To review"); one click to log a recommendation.

**P1-6. Customizable tables (Koyfin).**
Holdings and family tables: choose columns, reorder, save views ("Performance", "Valuation", "Risk"), density toggle (comfortable / compact 32px rows), export CSV/Excel, totals row pinned at the bottom.

**P1-7. Performance attribution.**
For a person and range: contribution by holding and by sector, allocation vs selection vs SPY (Brinson, simple version), top/bottom 5 contributors. Waterfall chart.

**P1-8. Alerts → Telegram.**
Price crosses level, move > X% in a day, weight above policy cap, earnings tomorrow. Managed in one list; delivered by the existing bot.

**P1-9. Earnings calendar.**
Next 30 days for all family holdings with date, time (BMO/AMC), implied move from options, exposure per person.

**P1-10. Recommendations workflow.**
Calls is empty, and it's the core of "how good is my advice". From any ticker (row, security page, Ctrl+K, Telegram): "Log recommendation" with direction, people, target, horizon, thesis in one line. Then each call gets its own result line vs SPY.

---

## P2. UX and information density

**P2-1. Navigation.** 8 top tabs + 8 What-if sub-tabs. Move to the 5-tab structure (Home, People, Stocks, Lab, Scores) from the design plan; Market overview lives under Stocks.
**P2-2. Info icons everywhere.** Every column header has an ⓘ. Move definitions into the header tooltip on hover and a single "Definitions" sheet; keep ⓘ only where a number is non-obvious.
**P2-3. Repeated paragraphs.** "Real results start on 30 Oct… import your transaction history" appears on several pages. Show it once as a dismissible banner with an Import button.
**P2-4. Two-line rows.** Holdings rows are ~57px with two lines in several cells. Offer compact mode (P1-6) and put secondary values (e.g. % under $) in a lighter style or a toggle ($ / %).
**P2-5. Keyboard-first.** Typing any letter anywhere opens symbol search (TradingView behavior); J/K row navigation; Enter opens the security page; "?" shows shortcuts; G then H/P/S/L/C to jump between tabs.
**P2-6. Hebrew suggestion chips in an English UI.** What-if examples mix Hebrew and English. Keep Hebrew input working, show English examples.
**P2-7. Range buttons.** Disabled ranges (1M–5Y when history is 3 days) look broken. Hide ranges longer than the data, and show "History: 3 trading days" next to the selector.
**P2-8. Empty states.** Council and Calls are empty pages with a paragraph. Each needs one clear action and a preview of what will appear (a ghosted example row).
**P2-9. Logos.** Several tickers fall back to letter tiles (A, C, D on the Holdings best/worst list) even when the logo loads in the table. Use the same logo source everywhere.
**P2-10. Mobile.** Not verified at 390px in this review. Tables need stacked rows, charts need touch crosshair, the top nav needs a bottom tab bar.

---

## P3. Polish

**P3-1.** Number formatting rules everywhere: thousands separators, 2 decimals for prices, 1 for % in prose and 2 in tables, "pp" for percentage points, true minus sign "−".
**P3-2.** Every timestamp shows timezone (Israel time) and market time where relevant ("16:00 ET / 23:00 IL").
**P3-3.** Tooltips and panels: Esc closes, focus returns to the trigger, no layout shift on open.
**P3-4.** Print/PDF of a person's page for meetings, one page, light theme.
**P3-5.** Console: no app errors (current errors come from a browser extension, not the app; keep it that way with an error boundary per page and a visible "Something failed, retry" line).
**P3-6.** Accessibility: charts get a text summary; all icon buttons labeled; focus ring visible on every control.

---

## Order of work
1. P0-1 … P0-14 (trust first; most are small).
2. P1-2 security page + P1-1 charts (the biggest jump toward TradingView/Koyfin feel).
3. P1-3 market overview, P1-4 heatmap, P1-5 watchlist.
4. P1-10 recommendations workflow, P1-6 tables, P1-7 attribution.
5. P2, then P3.
After each item: tests, browser check at 1440 and 390, screenshot, commit. Follow DESIGN.md for every visual decision.
