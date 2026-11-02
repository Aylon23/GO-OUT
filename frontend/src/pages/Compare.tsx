import { X } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { ChartSkeleton, colorFor, InlineLegend, SeriesChart, type SeriesDef } from "../components/chart/SeriesChart";
import { SymbolSearch } from "../components/SymbolSearch";
import { ErrorLine, InfoTip, Segmented, Skel } from "../components/ui";
import { api, type MetricRow, type Metrics } from "../lib/api";
import { pct, ratio } from "../lib/format";
import { useData } from "../lib/hooks";
import { useApp } from "../state";

const CODES = ["3M", "YTD", "1Y", "3Y", "5Y"];
const DEFAULT_SYMBOLS = ["SPY", "QQQ", "XLK"];
const SYMBOL_HINT: Record<string, string> = { XLK: "Tech sector", SPY: "S&P 500", QQQ: "Nasdaq 100", "^GSPC": "S&P 500 index", "^NDX": "Nasdaq 100 index" };

const load = <T,>(k: string, fallback: T): T => {
  try { const v = JSON.parse(localStorage.getItem(k) ?? ""); return v ?? fallback; } catch { return fallback; }
};
const save = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } };

export default function Compare() {
  const { portfolios, portfolioId, range, setRange } = useApp();
  const [symbols, setSymbols] = useState<string[]>(() => load("compareSymbols", DEFAULT_SYMBOLS));
  const [picked, setPicked] = useState<number[]>(() => load("comparePortfolios", [portfolioId]));
  const code = CODES.includes(range.range) ? range.range : "1Y";
  useEffect(() => save("compareSymbols", symbols), [symbols]);
  useEffect(() => save("comparePortfolios", picked), [picked]);
  // drop portfolios that no longer exist
  const pids = picked.filter((id) => portfolios.some((p) => p.id === id));
  const key = `${pids.join(",")}|${symbols.join(",")}|${code}`;
  const q = useData(() => api.metrics(pids, symbols, code), `mx-${key}`);
  const d = q.data;

  const colorOf = useMemo(() => {
    const idx = Object.fromEntries(portfolios.map((p, i) => [`p${p.id}`, i]));
    // portfolios get their own color, SPY and QQQ their fixed ones, anything else a dashed neutral line
    return (r: { key: string }, _i: number) => (r.key in idx ? `var(--p${(idx[r.key] % 8) + 1})` : r.key === "SPY" || r.key === "QQQ" ? colorFor(r.key) : "var(--muted)");
  }, [portfolios]);
  const dashOf = (r: MetricRow) => {
    if (r.kind === "portfolio" || r.key === "SPY" || r.key === "QQQ") return undefined;
    const extras = symbols.filter((x) => x !== "SPY" && x !== "QQQ");
    return ["4 3", "1 3", "7 3 1 3", "2 2"][Math.max(0, extras.indexOf(r.key)) % 4];
  };

  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Beta and valuation</h1>
        <Segmented label="Date range" accent size="sm" value={code} onChange={(v) => setRange({ range: v })} options={CODES.map((c) => ({ value: c, label: c }))} />
      </div>

      <div className="mt-8 grid gap-6 md:grid-cols-2">
        <div>
          <span className="label">Portfolios</span>
          <div className="flex flex-wrap gap-2">
            {portfolios.map((p, i) => {
              const on = pids.includes(p.id);
              return (
                <button key={p.id} type="button" aria-pressed={on} onClick={() => setPicked(on ? pids.filter((x) => x !== p.id) : [...pids, p.id])}
                  className={clsx("press flex h-8 items-center gap-2 rounded-sm border px-2.5 text-sm", on ? "border-line-strong bg-surface text-text" : "border-line text-faint hover:text-muted")}>
                  <span className="h-2 w-2 rounded-full" style={{ background: on ? `var(--p${(i % 8) + 1})` : "var(--line-strong)" }} />{p.name}
                </button>
              );
            })}
          </div>
        </div>
        <div>
          <span className="label">Stocks and indices</span>
          <div className="mb-2 flex flex-wrap gap-2">
            {symbols.map((s) => (
              <span key={s} className="flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-surface pl-2.5 pr-1 text-sm">
                <span className="font-mono font-semibold">{s}</span>
                {SYMBOL_HINT[s] && <span className="text-xs text-faint">{SYMBOL_HINT[s]}</span>}
                <button type="button" aria-label={`Remove ${s}`} className="btn-ghost h-6 w-6 px-0" onClick={() => setSymbols(symbols.filter((x) => x !== s))}><X size={12} /></button>
              </span>
            ))}
          </div>
          <div className="max-w-[360px]">
            <SymbolSearch placeholder="Add a stock or index" exclude={symbols} onSelect={(s) => setSymbols([...symbols, s.symbol].slice(0, 12))} />
          </div>
        </div>
      </div>

      {q.error && !d ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <CompareSkeleton /> : d.rows.length === 0 ? (
        <p className="mt-12 text-sm text-muted">Pick a portfolio with holdings, or add a stock or index to compare.</p>
      ) : (
        <div className={clsx("transition-opacity duration", q.loading && "opacity-60")}>
          <Answer d={d} current={`p${portfolioId}`} />
          {d.warnings.map((w) => <p key={w} className="mt-2 text-sm text-loss">{w}</p>)}
          <div className="mt-12 grid gap-14 lg:grid-cols-2">
            <section aria-labelledby="beta-h">
              <h2 id="beta-h" className="text-lg font-semibold">
                <InfoTip text="How much it tends to move when SPY moves 1%. Above 1 swings more than the market, below 1 less. Measured from daily returns over the range.">Beta vs SPY</InfoTip>
              </h2>
              <Bars rows={d.rows} value={(r) => r.beta} fmt={ratio} colorOf={colorOf} marker={1} markerLabel="Market = 1" />
            </section>
            <section aria-labelledby="pe-h">
              <h2 id="pe-h" className="text-lg font-semibold">
                <InfoTip text="Price divided by the last 12 months of earnings: how many years of today's profits you pay for. For a portfolio, total value divided by total earnings of its holdings.">P/E</InfoTip>
              </h2>
              <Bars rows={d.rows} value={(r) => r.trailing_pe} fmt={(v) => (v == null ? "–" : v.toFixed(1))} colorOf={colorOf}
                sub={(r) => (r.forward_pe ? `Forward ${r.forward_pe.toFixed(1)}` : "")} />
              <Coverage rows={d.rows} />
            </section>
          </div>
          <section className="mt-16" aria-labelledby="roll-h">
            <h2 id="roll-h" className="text-lg font-semibold">Beta over time</h2>
            <p className="mt-1 text-sm text-muted">Each point is the beta over the 6 months before it. Portfolios use today's holdings.</p>
            <div className="mt-4"><InlineLegend items={d.rows.map((r, i) => ({ key: r.key, label: r.label, color: colorOf(r, i), width: r.kind === "portfolio" ? 2 : 1.5, dash: !!dashOf(r) }))} /></div>
            <div className="mt-3">
              <SeriesChart ariaLabel="Rolling 6-month beta versus SPY" dates={d.rolling.dates} baseline={1} animateKey={key} height={300}
                series={d.rows.map((r, i): SeriesDef => ({ key: r.key, label: r.label, color: colorOf(r, i), width: r.kind === "portfolio" ? 2 : 1.5, dash: dashOf(r), values: d.rolling.series[r.key] ?? [] }))}
                format={(v) => ratio(v)} short={(v) => v.toFixed(1)} end={(v) => ratio(v)} />
            </div>
          </section>
          <section className="mt-16" aria-labelledby="tbl-h">
            <h2 id="tbl-h" className="text-lg font-semibold">All figures</h2>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead><tr className="border-b border-line text-xs text-muted">
                  <th className="py-2 text-left font-medium">Name</th><th className="py-2 text-right font-medium">Beta</th>
                  <th className="py-2 text-right font-medium">P/E</th><th className="py-2 text-right font-medium">Forward P/E</th>
                  <th className="py-2 text-right font-medium">Dividend yield</th>
                </tr></thead>
                <tbody>{d.rows.map((r, i) => (
                  <tr key={r.key} className="border-b border-line">
                    <td className="py-2.5"><span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: colorOf(r, i) }} />
                      <span className={clsx(r.kind === "symbol" && "font-mono font-semibold")}>{r.label}</span>
                      {r.kind === "symbol" && r.name && r.name !== r.label && <span className="truncate text-xs text-faint">{r.name}</span>}</span></td>
                    <td className="num py-2.5 text-right">{ratio(r.beta)}</td>
                    <td className="num py-2.5 text-right">{r.trailing_pe?.toFixed(1) ?? "–"}</td>
                    <td className="num py-2.5 text-right">{r.forward_pe?.toFixed(1) ?? "–"}</td>
                    <td className="num py-2.5 text-right">{r.dividend_yield == null ? "–" : pct(r.dividend_yield, 2, false)}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-faint">P/E and dividend yield from Yahoo Finance, refreshed daily. Indices use the ETF that tracks them.</p>
          </section>
        </div>
      )}
    </div>
  );
}

function Answer({ d, current }: { d: Metrics; current: string }) {
  const ps = d.rows.filter((r) => r.kind === "portfolio").sort((a, b) => (a.key === current ? -1 : b.key === current ? 1 : 0));
  const spy = d.rows.find((r) => r.key === "SPY");
  const tech = d.rows.find((r) => r.key === "XLK");
  if (!ps.length) return null;
  const p = ps[0];
  return (
    <p className="mt-10 max-w-[64ch] text-lg">
      {p.label} has a beta of <b className="num font-semibold">{ratio(p.beta)}</b>
      {p.trailing_pe != null && <> and a P/E of <b className="num font-semibold">{p.trailing_pe.toFixed(1)}</b></>}
      {spy?.trailing_pe != null && <>, against <span className="num">{spy.trailing_pe.toFixed(1)}</span> for the S&P 500</>}
      {tech?.trailing_pe != null && <> and <span className="num">{tech.trailing_pe.toFixed(1)}</span> for tech stocks</>}.
      {p.beta != null && <span className="text-muted"> It tends to move {p.beta > 1.05 ? "more than" : p.beta < 0.95 ? "less than" : "about as much as"} the market.</span>}
    </p>
  );
}

function Bars({ rows, value, fmt, colorOf, marker, markerLabel, sub }: {
  rows: MetricRow[]; value: (r: MetricRow) => number | null; fmt: (v: number | null) => string; colorOf: (r: MetricRow, i: number) => string;
  marker?: number; markerLabel?: string; sub?: (r: MetricRow) => string;
}) {
  const max = Math.max(...rows.map((r) => value(r) ?? 0), marker ?? 0) * 1.08 || 1;
  return (
    <ul className="mt-4">
      {rows.map((r, i) => {
        const v = value(r);
        return (
          <li key={r.key} className="grid grid-cols-[minmax(80px,140px)_1fr_auto] items-center gap-3 border-b border-line py-2.5 text-sm">
            <span className={clsx("truncate", r.kind === "symbol" ? "font-mono font-semibold" : "font-medium")}>{r.label}</span>
            <span className="relative h-2 rounded-full bg-sunken">
              {v != null && v > 0 && <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${(v / max) * 100}%`, background: colorOf(r, i) }} />}
              {marker !== undefined && <span aria-hidden title={markerLabel} className="absolute -inset-y-1 w-px bg-text opacity-40" style={{ left: `${(marker / max) * 100}%` }} />}
            </span>
            <span className="num w-24 text-right">
              {fmt(v)}{v == null && r.note && <span className="block text-xs text-faint">{r.note}</span>}
              {sub && sub(r) && <span className="block text-xs text-faint">{sub(r)}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Coverage({ rows }: { rows: MetricRow[] }) {
  const notes = rows.filter((r) => r.kind === "portfolio" && r.trailing_pe_missing && r.trailing_pe_missing.length);
  if (!notes.length) return null;
  return (
    <div className="mt-3 space-y-1 text-xs text-faint">
      {notes.map((r) => (
        <p key={r.key}>{r.label}: P/E covers {pct(r.trailing_pe_coverage ?? 0, 0, false)} of the value. No P/E (no earnings, or a fund without one) for {r.trailing_pe_missing!.join(", ")}.</p>
      ))}
    </div>
  );
}

function CompareSkeleton() {
  return (
    <div aria-hidden className="mt-10">
      <Skel className="h-7 w-[min(560px,100%)]" />
      <div className="mt-12 grid gap-14 lg:grid-cols-2">{[0, 1].map((i) => <div key={i} className="space-y-4"><Skel className="h-5 w-32" />{[0, 1, 2, 3].map((j) => <Skel key={j} className="h-5 w-full" />)}</div>)}</div>
      <div className="mt-14"><ChartSkeleton height={300} /></div>
    </div>
  );
}
