# Benchmark — design system

Every screen is built from these tokens. Components never use raw hex/rgb/oklch values; they use
CSS variables from `frontend/src/styles/tokens.css` through the Tailwind names below.

## Color (OKLCH only)

Strategy: **Restrained.** Warm stone neutrals (hue 70, chroma 0.005–0.01) plus one accent,
a desaturated burnt orange, on at most ~10% of the surface. Never pure black or white.

| Token | Tailwind | Light | Dark | Use |
|---|---|---|---|---|
| `--bg` | `bg-bg` | `oklch(0.972 0.006 75)` | `oklch(0.195 0.007 70)` | Page |
| `--surface` | `bg-surface` | `oklch(0.988 0.004 75)` | `oklch(0.232 0.008 70)` | Raised: panels, palette, popovers |
| `--sunken` | `bg-sunken` | `oklch(0.948 0.007 75)` | `oklch(0.165 0.006 70)` | Inputs, segmented track, skeletons |
| `--hover` | `bg-hover` | `oklch(0.935 0.008 75)` | `oklch(0.27 0.008 70)` | Row / item hover |
| `--line` | `border-line` | `oklch(0.895 0.008 75)` | `oklch(0.3 0.008 70)` | 1px hairlines |
| `--line-strong` | `border-line-strong` | `oklch(0.82 0.01 75)` | `oklch(0.38 0.01 70)` | Input borders |
| `--text` | `text-text` | `oklch(0.235 0.012 65)` | `oklch(0.93 0.006 75)` | Primary text |
| `--muted` | `text-muted` | `oklch(0.45 0.012 65)` | `oklch(0.74 0.008 75)` | Secondary text, axis labels |
| `--faint` | `text-faint` | `oklch(0.53 0.01 65)` | `oklch(0.64 0.008 75)` | Hints, placeholders (AA on bg) |
| `--accent` | `bg-accent` / `text-accent` | `oklch(0.53 0.125 48)` | `oklch(0.74 0.12 55)` | Portfolio line, primary button, focus ring, selected range |
| `--accent-text` | `text-accent-text` | `oklch(0.985 0.004 75)` | `oklch(0.2 0.02 55)` | Text on accent |
| `--accent-soft` | `bg-accent-soft` | `oklch(0.93 0.035 60)` | `oklch(0.32 0.05 55)` | Matched-search highlight background (rare) |
| `--gain` | `text-gain` | `oklch(0.48 0.085 150)` | `oklch(0.76 0.09 150)` | Positive signed numbers only |
| `--loss` | `text-loss` | `oklch(0.5 0.115 27)` | `oklch(0.74 0.1 27)` | Negative signed numbers only |
| `--series-spy` | `stroke-spy` | `oklch(0.62 0.012 70)` | `oklch(0.66 0.01 75)` | SPY line (mid stone) |
| `--series-qqq` | `stroke-qqq` | `oklch(0.5 0.045 250)` | `oklch(0.7 0.05 250)` | QQQ line (cool slate) |
| `--series-4/5` | | slate-green / dusty plum, low chroma | | Extra benchmarks |
| `--grid` | | `oklch(0.92 0.006 75)` | `oklch(0.27 0.007 70)` | Chart horizontal gridlines |

Heatmap: diverging scale built in code by mixing `--loss` → `--sunken` → `--gain` with
`color-mix(in oklch, …)`, capped at ±8% per month.

Rules
- Gain/loss color is used ONLY for signed numbers and the heatmap, and always with a + or − sign.
- Accent is used ONLY for: the portfolio series, the primary button, focus rings, the selected
  range / segment. Not for links, icons or headings.
- Contrast: all text tokens pass WCAG AA (4.5:1) on `--bg` and `--surface` in both themes
  (checked by `frontend/scripts/contrast.mjs`).

## Typography
Geist for UI, Geist Mono for tickers. Self-hosted from the `geist` npm package.
All numbers: `font-variant-numeric: tabular-nums` (`.num`).

Scale (ratio ≈ 1.25):

| Token | Size / line | Use |
|---|---|---|
| `text-xs` | 12 / 16 | Axis labels, table meta |
| `text-sm` | 13 / 20 | Tables, secondary line, labels |
| `text-base` | 15 / 24 | Body, inputs |
| `text-lg` | 19 / 28 | Section titles |
| `text-xl` | 24 / 32 | Page titles, panel headline |
| `text-2xl` | 30 / 38 | Answer sentence (mobile) |
| `text-3xl` | 38 / 46 | Answer sentence (desktop) |

Weights: 400 body, 500 labels/buttons, 600 tickers and emphasis. Headline sentence 500 with the
key figures at 600. No serif, no gradient text, letter-spacing −0.015em on 24px and up.

Number formatting (`src/lib/format.ts`): currency symbol + thousands separators; percentages to
1 decimal in prose and 2 in tables; signed values always show + or −. Compact form ($1.2M) only in
the headline sentence.

## Spacing
4px base. `1`=4, `2`=8, `3`=12, `4`=16, `5`=20, `6`=24, `8`=32, `10`=40, `12`=48, `16`=64.
Tight inside tables (row height 40, cell padding x 12). Roomy between sections (48–64).
Max content width 1200px, side gutter 16px on phones, 32px on desktop.

## Radii
`--r-sm` 6px (inputs, buttons, avatar), `--r-md` 10px (popovers, palette), `--r-lg` 14px (side panel, sheets).

## Elevation
Flat by default; grouping by whitespace and hairlines. Elevation only where it carries meaning:
- `shadow-pop`: popovers, search results, tooltips.
- `shadow-panel`: side panel, command palette, bottom sheet.
Shadows are warm-tinted, low alpha, never glows. No cards on the page itself, never nested.

## Motion
- `--ease-out`: `cubic-bezier(0.25, 1, 0.5, 1)` (ease-out-quart).
- `--dur-fast` 140ms (hover, press), `--dur` 220ms (panels, range transitions), `--dur-slow` 280ms.
- Animate opacity and transform only. Press feedback `scale(0.98)`.
- No bounce, no perpetual animation, no glow, no custom cursors.
- `prefers-reduced-motion: reduce` sets all durations to 0.

## Charts
- Recharts, no vertical gridlines, faint horizontal gridlines (`--grid`), axis text `text-xs text-muted`.
- Legend inline above the chart, never inside the plot.
- Portfolio = accent 2px; SPY = stone 1.5px; QQQ = slate 1.5px. Fixed everywhere.
- Crosshair tooltip lists every series for the hovered date in tabular numerals.
- End-of-line labels show each series' final value for the range.
- Range change: chart fades/translates in over 220ms.

## Components
- **Top bar** (sticky): app name, page nav, search trigger with ⌘K hint, theme toggle. Hairline bottom.
- **SymbolSearch**: input + results popover. Rows: avatar, ticker (mono 600), name (truncated),
  exchange · type as quiet text, price and day change right-aligned. Matched text bold, not colored.
- **Avatar**: 28px rounded square, first letter, background `oklch(0.9 0.03 <hash hue>)`
  (dark: `oklch(0.34 0.03 <hue>)`), derived deterministically from the ticker.
- **Segmented control**: sunken track, selected segment on `--surface` with accent text for range,
  or text color for view switches.
- **Buttons**: primary (accent fill), secondary (hairline border), text link. Height 36.
- **Inputs**: label above (text-sm, 500), helper below (text-xs muted), error below (text-xs loss).
- **Tables**: sticky header, right-aligned numbers, hairline row dividers, sortable headers with a
  small caret. Below 768px rows become stacked label/value blocks.
- **Side panel**: right, 440px, `shadow-panel`; below 768px a bottom sheet (max 88dvh).
- **Skeletons**: `bg-sunken` blocks the size of the final content. No spinners.
- **Empty states**: one sentence saying what goes here and a direct way to fill it.
- **Tooltips**: one plain-language line per metric.
- Icons: Phosphor, `weight="regular"` everywhere, 16px in UI, 18px in the top bar.
