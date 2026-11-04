# Benchmark — design system ("Ink & Signal")

Every screen is built from these tokens. Components never use raw hex/rgb/oklch values; they use
CSS variables from `frontend/src/styles/tokens.css` through the Tailwind names below.

## Direction
An investment house's research desk at night. Ink-blue graphite surfaces, one signal blue for
"you", a research-note serif for the answer sentence and page titles, an institutional sans for
everything that is data. The signature is **the benchmark line**: SPY is always a quiet dashed
rule that every other line is read against. Boldness is spent there and in the headline only.

Never: warm cream or stone neutrals, terracotta/orange accents, monospace tickers, gradient text,
side-stripe borders, decorative blur, identical card grids, arrows appended to button labels,
all-caps eyebrows, middle-dot meta strings in new UI, em dashes in UI copy.

## Color (OKLCH only)
Strategy: **Restrained.** Neutrals tinted to hue 262 (chroma 0.002–0.016) plus one accent, a
signal blue, on at most ~10% of the surface. Never pure black or white. Dark is the default and
gets full attention; light must pass contrast and stay clean.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `oklch(0.976 0.004 262)` | `oklch(0.158 0.011 262)` | Page |
| `--surface` | `oklch(0.994 0.002 262)` | `oklch(0.197 0.013 262)` | Raised: panels, palette, popovers |
| `--sunken` | `oklch(0.955 0.005 262)` | `oklch(0.135 0.01 262)` | Inputs, segmented track, skeletons |
| `--hover` | `oklch(0.94 0.007 262)` | `oklch(0.232 0.015 262)` | Row / item hover |
| `--line` / `--line-strong` | `0.905` / `0.83` L | `0.272` / `0.36` L | Hairlines / input borders |
| `--text` | `oklch(0.205 0.02 262)` | `oklch(0.948 0.006 262)` | Primary text |
| `--muted` | `oklch(0.43 0.018 262)` | `oklch(0.755 0.013 262)` | Secondary text, axis labels |
| `--faint` | `oklch(0.515 0.016 262)` | `oklch(0.655 0.015 262)` | Hints, placeholders (AA on bg) |
| `--accent` | `oklch(0.5 0.16 258)` | `oklch(0.74 0.125 250)` | "You" line, primary button, focus, selected range, nav rule |
| `--gain` | `oklch(0.49 0.105 162)` | `oklch(0.8 0.115 165)` | Positive signed numbers only |
| `--loss` | `oklch(0.52 0.165 25)` | `oklch(0.73 0.14 22)` | Negative signed numbers only |
| `--series-spy` | `oklch(0.36 0.012 262)` | `oklch(0.8 0.008 262)` | SPY: dashed `3 4`, 1.25px, neutral |
| `--series-qqq` | teal-slate | teal-slate | QQQ line |
| `--p1..p8` | | | One fixed hue per portfolio, charts and dots only |
| `--glass` | bg at 80% | bg at 78% | Header and chart tooltip only |

Rules
- Gain/loss color ONLY for signed numbers and the heatmap, always with a + or − sign.
- Accent ONLY for: the portfolio series, primary button, focus rings, selected range/segment,
  the active-page rule. Not for links, icons or headings.
- Contrast: all text tokens pass WCAG AA on bg, surface and sunken in both themes (`npm run contrast`).

## Typography
- **IBM Plex Sans** (400/500/600) for all UI, data and tickers. Hebrew names and ₪ come from
  IBM Plex Sans Hebrew under the same family name (unicode-range). Files in `src/styles/fonts/`.
- **Newsreader** (variable, optical size) for `#main h1` only: answer sentence and page titles,
  weight ~420, lining figures, balanced wrapping. Nothing else is serif.
- All numbers tabular. Tickers use `.font-mono`, mapped to Plex Sans 600 +0.015em. No monospace face.

| Style | Size / line | Use |
|---|---|---|
| `text-xs` | 12 / 16 | Axis labels, table meta |
| `text-sm` | 13 / 20 | Tables, secondary line, labels |
| `text-base` | 15 / 24 | Body, inputs |
| `text-lg` | 19 / 28 | Section titles (sans 600) |
| `h1.text-xl` | 28 / 34 serif | Page titles |
| `h1.text-2xl` / `md:text-3xl` | 34 / 40, 46 / 52 serif | Answer sentence |

Number formatting (`src/lib/format.ts`): currency symbol + thousands separators; percentages to
1 decimal in prose and 2 in tables; signed values always show + or −.

## Spacing
4px base. `1`=4, `2`=8, `3`=12, `4`=16, `5`=20, `6`=24, `8`=32, `10`=40, `12`=48, `16`=64.
Tight inside tables (row height 40, cell padding x 12). Roomy between sections (48–64).
Max content width 1200px, gutter 16px on phones, 32px on desktop.

## Radii
`--r-sm` 5px (inputs, buttons, avatar), `--r-md` 8px (popovers, palette, tooltip), `--r-lg` 12px (side panel, sheets).

## Elevation
Flat by default; grouping by whitespace and hairlines. `shadow-pop` (popovers, tooltips) and
`shadow-panel` (side panel, palette, sheet), cool-tinted with a 1px ring. `.glass` (blur 16px)
only on the sticky header and chart tooltip. No cards on the page, never nested.

## Motion
- `--ease-out` `cubic-bezier(0.22, 1, 0.36, 1)` (ease-out-quint). No bounce, no glow.
- `--dur-fast` 120ms (press), `--dur` 200ms (hover, color), `--dur-slow` 320ms (panels, page
  enter), `--dur-reveal` 640ms (the one orchestrated moment).
- Page change: `.route-in`, 8px rise + fade.
- Orchestrated moment: the answer sentence rises over 640ms, its second clause settles 140ms
  later; chart lines draw over 900ms (SPY first, the rest 120ms later); end labels fade in at 700ms.
- Nav: the 2px active rule slides between pages, transform only.
- Live values (`<Signed>`): when a value changes, one 900ms wash in gain/loss behind it, then nothing.
- Side panel / sheet exit: 200ms ease-in, faster than the 320ms entrance.
- Skeletons: slow shimmer (1.6s). Press `scale(0.975)`. Table rows tint on hover.
- Opacity and transform only. `prefers-reduced-motion` zeroes every duration and delay.

## Charts
- Recharts, no vertical gridlines, faint horizontal gridlines (`--grid`), axis text `text-xs text-muted`.
- Legend inline above the chart, never inside the plot; the SPY swatch is dashed too.
- Portfolio = accent 1.75px; SPY = neutral dashed 1.25px (the benchmark line); QQQ = teal-slate.
- Crosshair: dashed faint rule; tooltip on `.glass`, every series for the date, tabular numerals.
- End-of-line labels show each series' final value for the range.

## Components
- **Top bar** (sticky): app name, page nav, search trigger with ⌘K hint, theme toggle. Translucent `.glass` layer, hairline bottom; a 2px accent rule slides under the active page.
- **SymbolSearch**: input + results popover. Rows: avatar, ticker (`.font-mono` = Plex Sans 600, tracked; never a monospace face), name (truncated),
  exchange · type as quiet text, price and day change right-aligned. Matched text bold, not colored.
- **Avatar**: 28px rounded square (5px radius), first letter, background `oklch(0.9 0.03 <hash hue>)`
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
