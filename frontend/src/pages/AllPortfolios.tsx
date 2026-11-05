import { CaretDown, CaretUp, Plus } from "@phosphor-icons/react";
import { DayWord } from "../components/MarketChip";
import clsx from "clsx";
import { BackupWarning } from "../components/Backups";
import { useState } from "react";
import { FileText, FileXls } from "@phosphor-icons/react";
import { PeriodicReports } from "../components/PeriodicReports";
import { ReportPanel } from "../components/ReportPanel";
import { HoldingsImport } from "../components/HoldingsImport";
import { ChartSkeleton, colorFor, InlineLegend, SeriesChart, type SeriesDef } from "../components/chart/SeriesChart";
import { EmptyState, ErrorLine, InfoTip, Segmented, Signed, Skel, Updated } from "../components/ui";
import { api, ApiError, type Overview, type OverviewRow } from "../lib/api";
import { compactMoney, dateLong, money, pct, pp, rangeStartDate } from "../lib/format";
import { useData } from "../lib/hooks";
import { useApp } from "../state";
import { rangePhrase } from "./Dashboard";

const CODES = ["1M", "3M", "YTD", "1Y", "3Y", "5Y", "MAX"];
const pColor = (i: number) => `var(--p${(i % 8) + 1})`;

export default function AllPortfolios() {
  const { range, setRange, setPortfolio, go, reloadPortfolios } = useApp();
  const code = CODES.includes(range.range) ? range.range : "1Y";
  const q = useData(() => api.overview(code), `ov-${code}`);
  const d = q.data;
  const open = (r: OverviewRow, page: "dashboard" | "transactions" = "dashboard") => { setPortfolio(r.id); go(page); };
  const [importing, setImporting] = useState(false);
  const [report, setReport] = useState(false);
  // a range is offered when at least one portfolio has real history that far back
  const firstTracked = d?.rows.filter((r) => r.tracking_since).map((r) => r.tracking_since!).sort()[0];
  const tooEarly = (c: string) => { const st = rangeStartDate(c); return !!(firstTracked && st && st < firstTracked); };

  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-10 md:px-8 md:pt-16">
      <BackupWarning />
      {q.error && !d ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <OverviewSkeleton /> : (
        <div className={clsx("transition-opacity duration", q.loading && !q.stale && "opacity-60")}>
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 [&>h1]:min-w-0">
            <Headline d={d} code={code} />
            <span className="flex shrink-0 items-center gap-3"><Updated at={q.updatedAt} refreshing={q.stale && q.loading} />{!d.rows.every((r) => r.empty) && <button type="button" className="btn-ghost shrink-0" onClick={() => setReport(true)}><FileText size={16} /> Report</button>}</span>
          </div>
          {d.rows.every((r) => r.empty) ? (
            <EmptyState title="No holdings yet in any portfolio." body="Import an Excel file with your portfolios (one table per portfolio, under its name), or add a portfolio and fill it in.">
              <button type="button" className="btn-primary" onClick={() => setImporting(true)}><FileXls size={16} /> Import from Excel</button>
              <NewPortfolio onCreated={async (id) => { await reloadPortfolios(); setPortfolio(id); go("transactions"); }} />
            </EmptyState>
          ) : (
            <>
              <Insights d={d} />
              <section className="mt-12" aria-label="Comparison chart">
                <div className="flex flex-col-reverse gap-3 md:flex-row md:items-center md:justify-between">
                  <InlineLegend items={legend(d)} />
                  <Segmented label="Date range" accent size="sm" value={tooEarly(code) ? "MAX" : code} onChange={(v) => setRange({ range: v })}
                    options={CODES.map((c) => ({ value: c, label: c, disabled: tooEarly(c), title: tooEarly(c) ? `Tracking started ${dateLong(firstTracked!)}` : undefined }))} />
                </div>
                <div className="mt-4">
                  <SeriesChart ariaLabel="All portfolios versus SPY and QQQ, rebased to 100" dates={d.chart.dates} series={chartSeries(d)} baseline={100}
                    animateKey={code} height={340} format={(v) => `${v.toFixed(1)}  (${pct(v / 100 - 1, 1)})`} short={(v) => pct(v / 100 - 1, 0)} end={(v) => pct(v / 100 - 1, 1)} />
                </div>
                <p className="mt-2 max-w-[80ch] text-xs text-faint">
                  Real results, time-weighted. Stock performance only, currency effects excluded. A portfolio added recently starts on the day it was added;
                  import its transaction history to include earlier years.
                </p>
              </section>
              <PortfolioTable d={d} code={code} onOpen={open} />
              <div className="mt-6 flex flex-wrap items-center gap-4">
                <button type="button" className="btn-secondary" onClick={() => setImporting((x) => !x)}><FileXls size={16} /> Import from Excel</button>
                <NewPortfolio onCreated={async (id) => { await reloadPortfolios(); setPortfolio(id); go("transactions"); }} />
              </div>
              <PeriodicReports />
            </>
          )}
        </div>
      )}
      {importing && <HoldingsImport onClose={() => setImporting(false)} />}
      {report && <ReportPanel all title="Report: all portfolios" onClose={() => setReport(false)} />}
    </div>
  );
}

function legend(d: Overview) {
  const items = d.rows.filter((r) => !r.empty && d.chart.perf[`p${r.id}`]).map((r) => ({
    key: `p${r.id}`, label: r.name, color: pColor(d.rows.indexOf(r)), width: 2,
  }));
  return [...items, ...d.benchmarks.map((b, i) => ({ key: b.symbol, label: b.symbol, color: colorFor(b.symbol, i), width: 1.5 }))];
}

function chartSeries(d: Overview): SeriesDef[] {
  const out: SeriesDef[] = [];
  d.rows.forEach((r, i) => {
    const v = d.chart.perf[`p${r.id}`];
    if (v) out.push({ key: `p${r.id}`, label: r.name, color: pColor(i), width: 2, values: v });
  });
  d.benchmarks.forEach((b, i) => {
    const v = d.chart.perf[b.symbol];
    if (v) out.push({ key: b.symbol, label: b.symbol, color: colorFor(b.symbol, i), width: 1.5, values: v });
  });
  return out;
}

function Headline({ d, code }: { d: Overview; code: string }) {
  const tracked = d.rows.filter((r) => !r.empty && r.return != null && r.bench?.SPY != null);
  const live = tracked.filter((r) => r.compare_ok !== false);
  const maxDays = Math.max(0, ...tracked.map((r) => r.trading_days ?? 0));
  const beat = live.filter((r) => (r.return as number) > (r.bench!.SPY as number)).length;
  const n = d.rows.length;
  const gain = d.rows.reduce((s, r) => s + (r.cost_gain ?? 0), 0);
  const realized = d.rows.reduce((s, r) => s + (r.realized ?? 0), 0);
  const allShort = live.length > 0 && live.every((r) => r.short_history);
  return (
    <>
      <h1 className="max-w-[32ch] text-2xl font-medium md:text-3xl">
        {n} portfolio{n === 1 ? "" : "s"}, <b className="font-semibold">{compactMoney(d.total_value ?? 0, d.base)}</b> in total.
        {live.length > 0 && (
          <span className="text-muted"> {beat === live.length ? (live.length === 1 ? "It is" : "All are") : beat === 0 ? "None are" : `${beat} of ${live.length} are`} ahead of the S&P 500 {allShort && d.since ? `since ${dateLong(d.since)}` : rangePhrase(code, d.since ?? "")}.</span>
        )}
        {live.length === 0 && tracked.length > 0 && (
          <span className="text-muted"> Too early to compare with the S&P 500 ({maxDays} of {d.min_trading_days} trading days).</span>
        )}
      </h1>
      <p className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
        <span>Gain vs average cost <Signed value={gain} className="font-medium">{money(gain, d.base, { signed: true, decimals: 0 })}</Signed></span>
        {Math.abs(realized) >= 0.5 && <span className="inline-flex items-center gap-2"><span aria-hidden className="text-faint">·</span>Realized <Signed value={realized} className="font-medium">{money(realized, d.base, { signed: true, decimals: 0 })}</Signed></span>}
      </p>
    </>
  );
}

type SortKey = "name" | "value" | "gain" | "vs" | "today" | "activity";

function Spark({ values }: { values: (number | null)[] }) {
  const v = values.filter((x): x is number => x != null);
  if (v.length < 2) return <span className="text-xs text-faint">–</span>;
  const w = 80, h = 24, lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 2 - ((x - lo) / span) * (h - 4)).toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden className={v[v.length - 1] >= v[0] ? "text-gain" : "text-loss"}>
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function Insights({ d }: { d: Overview }) {
  if (!d.insights?.length) return null;
  const dot = { gain: "bg-gain", loss: "bg-loss", warn: "bg-accent", info: "bg-muted" } as const;
  return (
    <section className="mt-10" aria-label="Insights">
      <ul className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
        {d.insights.map((i, k) => (
          <li key={k} className="flex items-start gap-2.5 border-t border-line pt-2.5 text-sm">
            <span className={clsx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", dot[i.tone])} aria-hidden />
            <span>{i.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PortfolioTable({ d, onOpen }: { d: Overview; code: string; onOpen: (r: OverviewRow, page?: "dashboard" | "transactions") => void }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "value", dir: -1 });
  const min = d.min_trading_days ?? 20;
  const early = (r: OverviewRow) => (r.trading_days ?? 0) < min;
  const val = (r: OverviewRow, k: SortKey): number | string | null => {
    switch (k) {
      case "name": return r.name;
      case "value": return r.value ?? null;
      case "gain": return r.cost_gain_amt ?? null;
      case "vs": return early(r) ? null : r.since_vs_spy ?? null;
      case "today": return r.today_change ?? null;
      case "activity": return r.last_activity ?? null;
    }
  };
  const live = d.rows.filter((r) => !r.empty);
  const rows = [...live].sort((a, b) => {
    const av = val(a, sort.key), bv = val(b, sort.key);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (typeof av === "string" ? av.localeCompare(String(bv)) : (av as number) - (bv as number)) * sort.dir;
  });
  const color = (r: OverviewRow) => pColor(d.rows.indexOf(r));
  const Th = ({ k, children, right, className, tip }: { k: SortKey; children: React.ReactNode; right?: boolean; className?: string; tip?: string }) => (
    <th scope="col" aria-sort={sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
      className={clsx("sticky top-14 z-10 border-b border-line bg-bg py-2 text-xs font-medium text-muted", right ? "pl-3 text-right" : "text-left", className)}>
      <button type="button" className={clsx("press inline-flex items-center gap-1 rounded-sm hover:text-text", right && "flex-row-reverse")}
        onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? (s.dir === 1 ? -1 : 1) : k === "name" ? 1 : -1 }))}>
        {sort.key === k ? (sort.dir === 1 ? <CaretUp size={10} /> : <CaretDown size={10} />) : <span className="w-[10px]" />}
        {children}
      </button>
      {tip && <span className="ml-1 inline-flex align-middle"><InfoTip text={tip} /></span>}
    </th>
  );
  const vsCell = (r: OverviewRow) => early(r)
    ? <span className="text-xs text-faint">Too early · {r.trading_days ?? 0} day{r.trading_days === 1 ? "" : "s"}</span>
    : <span className="leading-5"><Signed value={r.since_vs_spy} className="block">{pp(r.since_vs_spy, 1)}</Signed>
      <span className="block text-xs text-faint">{pct(r.since_return, 1)} vs {pct(r.since_spy, 1)}</span></span>;
  return (
    <section className="mt-14 text-sm" aria-label="Portfolios">
      <table className="hidden w-full border-separate border-spacing-0 md:table">
        <thead><tr>
          <Th k="name">Person</Th>
          <th scope="col" className="sticky top-14 z-10 hidden border-b border-line bg-bg py-2 pl-3 text-left text-xs font-medium text-muted lg:table-cell">Since tracking</th>
          <Th k="value" right>Value</Th>
          <Th k="gain" right tip="Market value of open positions minus what they cost (your average price).">Gain since bought</Th>
          <Th k="vs" right tip={`Return since tracking began minus the S&P 500 (SPY, dividends included) over exactly the same days. Shown after ${min} trading days.`}><span className="block leading-4">vs S&P 500<span className="block font-normal text-faint">since start</span></span></Th>
          <Th k="today" right><DayWord /></Th>
          <Th k="activity" right className="hidden lg:table-cell">Last activity</Th>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} tabIndex={0} onClick={() => onOpen(r)} onKeyDown={(e) => { if (e.key === "Enter") onOpen(r); }} className="cursor-pointer transition-colors duration-fast hover:bg-hover">
              <td className="border-b border-line py-3">
                <span className="flex min-w-0 items-center gap-2.5">
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color(r) }} />
                  <span className="min-w-0"><span className="block truncate font-medium">{r.name}</span>
                    <span className="block truncate text-xs text-faint">{r.note || `${r.holdings} holding${r.holdings === 1 ? "" : "s"}`}</span></span>
                </span>
              </td>
              <td className="hidden border-b border-line py-3 pl-3 lg:table-cell"><Spark values={r.spark ?? []} /></td>
              <td className="num border-b border-line py-3 pl-3 text-right font-medium">{money(r.value, d.base, { decimals: 0 })}</td>
              <td className="num border-b border-line py-3 pl-3 text-right"><span className="leading-5">
                <Signed value={r.cost_gain_amt} className="block">{money(r.cost_gain_amt, d.base, { signed: true, decimals: 0 })}</Signed>
                <Signed value={r.cost_gain_pct} className="block text-xs">{pct(r.cost_gain_pct, 1)}</Signed></span></td>
              <td className="num border-b border-line py-3 pl-3 text-right">{vsCell(r)}</td>
              <td className="num border-b border-line py-3 pl-3 text-right"><span className="leading-5">
                <Signed value={r.today_change} className="block">{money(r.today_change, d.base, { signed: true, decimals: 0 })}</Signed>
                <Signed value={r.today_pct} className="block text-xs">{pct(r.today_pct, 2)}</Signed></span></td>
              <td className="num hidden border-b border-line py-3 pl-3 text-right text-muted lg:table-cell">{r.last_activity ? dateLong(r.last_activity) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {/* stacked below 768px */}
      <div className="md:hidden">
        {rows.map((r) => (
          <div key={r.id} role="button" tabIndex={0} onClick={() => onOpen(r)} onKeyDown={(e) => { if (e.key === "Enter") onOpen(r); }}
            className="grid cursor-pointer grid-cols-2 gap-x-3 gap-y-1 border-b border-line py-3 transition-colors duration-fast hover:bg-hover">
            <span className="col-span-2 flex items-center gap-2.5">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color(r) }} />
              <span className="font-medium">{r.name}</span>
              <span className="ml-auto"><Spark values={r.spark ?? []} /></span>
              <span className="num font-medium">{money(r.value, d.base, { decimals: 0 })}</span>
            </span>
            <Kv k="Gain since bought" v={<Signed value={r.cost_gain_amt}>{money(r.cost_gain_amt, d.base, { signed: true, decimals: 0 })} ({pct(r.cost_gain_pct, 1)})</Signed>} />
            <Kv k="vs S&P 500 since start" v={early(r) ? <span className="text-faint">Too early, {r.trading_days ?? 0} of {min} days</span> : <Signed value={r.since_vs_spy}>{pp(r.since_vs_spy, 1)}</Signed>} />
            <Kv k={<DayWord />} v={<Signed value={r.today_change}>{money(r.today_change, d.base, { signed: true, decimals: 0 })} ({pct(r.today_pct, 2)})</Signed>} />
            <Kv k="Last activity" v={r.last_activity ? dateLong(r.last_activity) : "–"} />
          </div>
        ))}
      </div>
      {d.rows.filter((r) => r.empty).map((r) => (
        <div key={r.id} className="flex items-center gap-3 border-b border-line py-3">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color(r) }} />
          <span className="font-medium">{r.name}</span>
          <span className="text-faint">Empty.</span>
          <button type="button" className="link ml-auto text-sm" onClick={() => onOpen(r, "transactions")}>Add holdings</button>
        </div>
      ))}
    </section>
  );
}

const Kv = ({ k, v }: { k: React.ReactNode; v: React.ReactNode }) => (
  <div className="flex justify-between gap-2 text-xs"><span className="text-faint">{k}</span><span className="num text-text">{v}</span></div>
);

export function NewPortfolio({ onCreated }: { onCreated: (id: number) => void }) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <form className="flex flex-wrap items-start gap-2" onSubmit={async (e) => {
      e.preventDefault();
      try { const p = await api.createPortfolio(name); setName(""); onCreated(p.id); } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't create it."); }
    }}>
      <div>
        <label htmlFor="np-name" className="sr-only">Portfolio name</label>
        <input id="np-name" className="field w-56" placeholder="New portfolio name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
        {err && <p className="error-text">{err}</p>}
      </div>
      <button type="submit" className="btn-secondary" disabled={!name.trim()}><Plus size={16} /> Add portfolio</button>
    </form>
  );
}

function OverviewSkeleton() {
  return (
    <div aria-hidden>
      <Skel className="h-10 w-[min(560px,100%)]" /><Skel className="mt-5 h-4 w-[min(360px,80%)]" />
      <div className="mt-12"><ChartSkeleton height={340} /></div>
      <div className="mt-12">{[0, 1, 2].map((i) => <div key={i} className="flex justify-between border-b border-line py-4"><Skel className="h-4 w-40" /><Skel className="h-4 w-24" /></div>)}</div>
    </div>
  );
}

