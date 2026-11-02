import { useMemo } from "react";
import { CartesianGrid, Customized, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dateLong, monthName } from "../../lib/format";

export interface SeriesDef {
  key: string;
  label: string;
  color: string; // CSS var reference, e.g. var(--accent)
  width?: number;
  dash?: string;
  step?: boolean;
  values: (number | null)[];
}

interface Props {
  dates: string[];
  series: SeriesDef[];
  format: (v: number) => string;
  /** Short formatter for axis ticks and end labels. */
  short?: (v: number) => string;
  /** Formatter for end-of-line labels (defaults to `short`). */
  end?: (v: number) => string;
  height?: number;
  baseline?: number;
  animateKey?: string;
  ariaLabel: string;
}

export const SERIES_COLOR: Record<string, string> = {
  portfolio: "var(--accent)",
  SPY: "var(--series-spy)",
  QQQ: "var(--series-qqq)",
};
const EXTRA = ["var(--series-4)", "var(--series-5)", "var(--series-spy)"];
export function colorFor(key: string, i = 0) {
  return SERIES_COLOR[key] ?? EXTRA[i % EXTRA.length];
}

export function SeriesChart({ dates, series, format, short, end, height = 320, baseline, animateKey, ariaLabel }: Props) {
  const fmtShort = short ?? format;
  const data = useMemo(
    () => dates.map((d, i) => { const row: Record<string, string | number | null> = { date: d }; series.forEach((s) => { row[s.key] = s.values[i]; }); return row; }),
    [dates, series],
  );
  const spanDays = dates.length > 1 ? (Date.parse(dates[dates.length - 1]) - Date.parse(dates[0])) / 864e5 : 0;
  const tickFmt = (d: string) => {
    const [y, m, day] = d.split("-").map(Number);
    if (spanDays > 3 * 365) return String(y);
    if (spanDays > 120) return `${monthName(m)}${m === 1 ? ` ${String(y).slice(2)}` : ""}`;
    return `${day} ${monthName(m)}`;
  };
  const ticks = useMemo(() => {
    if (dates.length < 2) return dates;
    const out: string[] = [];
    let last = "";
    for (const d of dates) {
      const key = spanDays > 3 * 365 ? d.slice(0, 4) : spanDays > 120 ? d.slice(0, 7) : "";
      if (key && key !== last) { out.push(d); last = key; }
    }
    if (!out.length || spanDays <= 120) {
      const n = 5;
      // few dates: evenly spaced picks can repeat, and repeated ticks break the axis
      return Array.from(new Set(Array.from({ length: n }, (_, i) => dates[Math.round((i * (dates.length - 1)) / (n - 1))])));
    }
    const step = Math.ceil(out.length / 7);
    return Array.from(new Set(out.filter((_, i) => i % step === 0).slice(out[0] === dates[0] && out.length > 1 ? 1 : 0)));
  }, [dates, spanDays]);

  const axis = useMemo(() => niceAxis(series.flatMap((s) => s.values), baseline), [series, baseline]);
  // on small ranges the short format can repeat a label ("0%", "0%"); use the precise one then
  const shortLabels = axis.ticks.map((t) => fmtShort(t));
  const tickFmtY = new Set(shortLabels).size < shortLabels.length ? (end ?? format) : fmtShort;
  const ordered = [...series].sort((a, b) => (a.key === "portfolio" ? 1 : b.key === "portfolio" ? -1 : 0)); // portfolio drawn on top

  return (
    <div key={animateKey} className="fade-in" role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 72, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="date" ticks={ticks} tickFormatter={tickFmt} tickLine={false} axisLine={false}
            tick={{ fill: "var(--muted)", fontSize: 12 }} minTickGap={24} dy={6} />
          <YAxis orientation="left" width={64} tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 12 }}
            tickFormatter={(v) => tickFmtY(v)} domain={axis.domain} ticks={axis.ticks} interval={0} />
          {baseline !== undefined && <ReferenceLine y={baseline} stroke="var(--line-strong)" strokeDasharray="2 3" />}
          <Tooltip
            cursor={{ stroke: "var(--line-strong)", strokeWidth: 1 }}
            isAnimationActive={false}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              return (
                <div className="num min-w-[180px] rounded-sm bg-surface px-3 py-2.5 text-sm shadow-pop">
                  <div className="mb-1.5 text-xs text-muted">{dateLong(String(label))}</div>
                  {series.map((s) => {
                    const p = payload.find((x) => x.dataKey === s.key);
                    const v = p?.value as number | null | undefined;
                    return (
                      <div key={s.key} className="flex items-center justify-between gap-4 py-0.5">
                        <span className="flex items-center gap-2 text-muted">
                          <span className="inline-block h-0.5 w-3 rounded-full" style={{ background: s.color }} />{s.label}
                        </span>
                        <span className="text-text">{v === null || v === undefined ? "–" : format(v)}</span>
                      </div>
                    );
                  })}
                </div>
              );
            }}
          />
          {ordered.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.label} type={s.step ? "stepAfter" : "linear"} stroke={s.color} strokeWidth={s.width ?? 1.5}
              strokeDasharray={s.dash} dot={false} activeDot={{ r: 3, strokeWidth: 0, fill: s.color }} isAnimationActive={false} connectNulls />
          ))}
          <Customized component={(p: unknown) => <EndLabels {...(p as EndLabelProps)} series={series} fmt={end ?? fmtShort} />} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface EndLabelProps {
  formattedGraphicalItems?: { item: { props: { dataKey: string } }; props: { points: { x: number; y: number | null; value: number | null }[] } }[];
  offset?: { left: number; width: number };
  series: SeriesDef[];
  fmt: (v: number) => string;
}

/** Final value of each series at the right edge, nudged apart so they never overlap. */
function EndLabels({ formattedGraphicalItems, offset, series, fmt }: EndLabelProps) {
  if (!formattedGraphicalItems || !offset) return null;
  const labels = formattedGraphicalItems
    .map((g) => {
      const pts = g.props.points.filter((p) => p.y !== null && p.value !== null && p.value !== undefined);
      const last = pts[pts.length - 1];
      const s = series.find((x) => x.key === g.item.props.dataKey);
      if (!last || !s || s.step) return null;
      return { key: s.key, color: s.color, y: last.y as number, value: last.value as number, strong: s.key === "portfolio" };
    })
    .filter((x): x is NonNullable<typeof x> => !!x)
    .sort((a, b) => a.y - b.y);
  const gap = 15;
  for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < gap) labels[i].y = labels[i - 1].y + gap;
  const x = offset.left + offset.width + 8;
  return (
    <g>
      {labels.map((l) => (
        <text key={l.key} x={x} y={l.y} dy={4} fontSize={12} fill={l.color} fontWeight={l.strong ? 600 : 500} style={{ fontVariantNumeric: "tabular-nums" }}>
          {fmt(l.value)}
        </text>
      ))}
    </g>
  );
}

export function InlineLegend({ items }: { items: { key: string; label: string; color: string; value?: string; width?: number; dash?: boolean }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm">
      {items.map((it) => (
        <li key={it.key} className="flex items-center gap-2">
          <svg width="16" height="6" aria-hidden><line x1="0" y1="3" x2="16" y2="3" stroke={it.color} strokeWidth={it.width ?? 1.5} strokeDasharray={it.dash ? "3 2" : undefined} /></svg>
          <span className="text-muted">{it.label}</span>
          {it.value && <span className="num font-medium text-text">{it.value}</span>}
        </li>
      ))}
    </ul>
  );
}

export function ChartSkeleton({ height = 320 }: { height?: number }) {
  return (
    <div aria-hidden className="relative" style={{ height }}>
      <div className="absolute inset-0 flex flex-col justify-between py-2 pl-16 pr-[72px]">
        {Array.from({ length: 5 }).map((_, i) => <div key={i} className="h-px bg-line" />)}
      </div>
      <div className="skeleton absolute bottom-10 left-16 right-[72px] top-10 opacity-60" />
    </div>
  );
}

/** Round axis bounds and evenly spaced ticks (1, 2, 2.5, 5 x 10^n steps). */
export function niceAxis(values: (number | null)[], include?: number): { domain: [number, number]; ticks: number[] } {
  const v = values.filter((x): x is number => x !== null && isFinite(x));
  if (include !== undefined) v.push(include);
  if (!v.length) return { domain: [0, 1], ticks: [0, 1] };
  let lo = Math.min(...v), hi = Math.max(...v);
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / 5;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let t = start; t <= end + step / 1e6; t += step) ticks.push(Number(t.toFixed(10)));
  return { domain: [start, end], ticks };
}
