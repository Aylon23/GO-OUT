import { Plus } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { FileText, FileXls } from "@phosphor-icons/react";
import { ReportPanel } from "../components/ReportPanel";
import { HoldingsImport } from "../components/HoldingsImport";
import { ChartSkeleton, colorFor, InlineLegend, SeriesChart, type SeriesDef } from "../components/chart/SeriesChart";
import { EmptyState, ErrorLine, InfoTip, Segmented, Signed, Skel } from "../components/ui";
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
      {q.error && !d ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <OverviewSkeleton /> : (
        <div className={clsx("transition-opacity duration", q.loading && "opacity-60")}>
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 [&>h1]:min-w-0">
            <Headline d={d} code={code} />
            {!d.rows.every((r) => r.empty) && <button type="button" className="btn-ghost shrink-0" onClick={() => setReport(true)}><FileText size={16} /> Report</button>}
          </div>
          {d.rows.every((r) => r.empty) ? (
            <EmptyState title="No holdings yet in any portfolio." body="Import an Excel file with your portfolios (one table per portfolio, under its name), or add a portfolio and fill it in.">
              <button type="button" className="btn-primary" onClick={() => setImporting(true)}><FileXls size={16} /> Import from Excel</button>
              <NewPortfolio onCreated={async (id) => { await reloadPortfolios(); setPortfolio(id); go("transactions"); }} />
            </EmptyState>
          ) : (
            <>
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
                  Real results, time-weighted, in {d.base === "USD" ? "dollars" : "shekels"}. A portfolio added recently starts on the day it was added;
                  import its transaction history to include earlier years.
                </p>
              </section>
              <PortfolioTable d={d} code={code} onOpen={open} />
              <div className="mt-6 flex flex-wrap items-center gap-4">
                <button type="button" className="btn-secondary" onClick={() => setImporting((x) => !x)}><FileXls size={16} /> Import from Excel</button>
                <NewPortfolio onCreated={async (id) => { await reloadPortfolios(); setPortfolio(id); go("transactions"); }} />
              </div>
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
  const live = d.rows.filter((r) => !r.empty && r.return != null && r.bench?.SPY != null);
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
      </h1>
      <p className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
        <span>Gain vs average cost <Signed value={gain} className="font-medium">{money(gain, d.base, { signed: true, decimals: 0 })}</Signed></span>
        {Math.abs(realized) >= 0.5 && <span className="inline-flex items-center gap-2"><span aria-hidden className="text-faint">·</span>Realized <Signed value={realized} className="font-medium">{money(realized, d.base, { signed: true, decimals: 0 })}</Signed></span>}
      </p>
    </>
  );
}

function PortfolioTable({ d, code, onOpen }: { d: Overview; code: string; onOpen: (r: OverviewRow, page?: "dashboard" | "transactions") => void }) {
  const cols = "grid-cols-[minmax(160px,1.6fr)_120px_150px_110px_110px_90px_120px]";
  return (
    <section className="mt-14 text-sm" aria-label="Portfolios">
      <div role="row" className={clsx("sticky top-14 z-10 hidden gap-3 border-b border-line bg-bg py-2 text-xs font-medium text-muted lg:grid", cols)}>
        <span role="columnheader">Portfolio</span>
        <span role="columnheader" className="text-right">Value</span>
        <span role="columnheader" className="flex justify-end"><InfoTip text="Market value of open positions minus what they cost, using the average price you entered.">Gain vs cost</InfoTip></span>
        <span role="columnheader" className="flex justify-end"><InfoTip text={`Real time-weighted return ${rangePhrase(code, "")}, or since the portfolio was added if that is later.`}>Return</InfoTip></span>
        <span role="columnheader" className="flex justify-end"><InfoTip text="Return minus the S&P 500 (SPY, dividends included) over exactly the same days.">vs S&P 500</InfoTip></span>
        <span role="columnheader" className="text-right">Today</span>
        <span role="columnheader" className="text-right">Tracked since</span>
      </div>
      {d.rows.map((r, i) => r.empty ? (
        <div key={r.id} className="flex items-center gap-3 border-b border-line py-3">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: pColor(i) }} />
          <span className="font-medium">{r.name}</span>
          <span className="text-faint">Empty.</span>
          <button type="button" className="link ml-auto text-sm" onClick={() => onOpen(r, "transactions")}>Add holdings</button>
        </div>
      ) : (
        <div key={r.id} role="row" tabIndex={0} onClick={() => onOpen(r)} onKeyDown={(e) => { if (e.key === "Enter") onOpen(r); }}
          className="cursor-pointer border-b border-line transition-colors duration-fast hover:bg-hover">
          {/* stacked below 1024px */}
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 py-3 lg:hidden">
            <span className="col-span-2 flex items-center gap-2.5">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: pColor(i) }} />
              <span className="font-medium">{r.name}</span>
              <span className="num ml-auto font-medium">{money(r.value, d.base, { decimals: 0 })}</span>
            </span>
            <Kv k="Gain vs cost" v={<Signed value={r.cost_gain}>{money(r.cost_gain, d.base, { signed: true, decimals: 0 })} ({pct(r.cost_gain_pct, 1)})</Signed>} />
            <Kv k="Return" v={<Signed value={r.return}>{pct(r.return, 1)}</Signed>} />
            <Kv k="vs S&P 500" v={<Signed value={diff(r)}>{pp(diff(r), 1)}</Signed>} />
            <Kv k="Today" v={<Signed value={r.today_change}>{money(r.today_change, d.base, { signed: true, decimals: 0 })} ({pct(r.today_pct, 2)})</Signed>} />
          </div>
          <div className={clsx("hidden items-center gap-3 py-3 lg:grid", cols)}>
            <span className="flex min-w-0 items-center gap-2.5">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: pColor(i) }} />
              <span className="min-w-0">
                <span className="block truncate font-medium">{r.name}</span>
                <span className="block truncate text-xs text-faint">{r.note || `${r.holdings} holding${r.holdings === 1 ? "" : "s"}`}</span>
              </span>
            </span>
            <span className="num text-right font-medium">{money(r.value, d.base, { decimals: 0 })}</span>
            <span className="text-right leading-5">
              <Signed value={r.cost_gain} className="block">{money(r.cost_gain, d.base, { signed: true, decimals: 0 })}</Signed>
              <Signed value={r.cost_gain_pct} className="block text-xs">{pct(r.cost_gain_pct, 1)}</Signed>
            </span>
            <span className="text-right leading-5">
              <Signed value={r.return} className="block">{pct(r.return, 1)}</Signed>
              {r.short_history && r.period_start && <span className="block text-xs text-faint">since {dateLong(r.period_start)}</span>}
            </span>
            <Signed value={diff(r)} className="text-right">{pp(diff(r), 1)}</Signed>
            <span className="text-right leading-5">
              <Signed value={r.today_change} className="block">{money(r.today_change, d.base, { signed: true, decimals: 0 })}</Signed>
              <Signed value={r.today_pct} className="block text-xs">{pct(r.today_pct, 2)}</Signed>
            </span>
            <span className="num text-right text-muted">{r.tracking_since ? dateLong(r.tracking_since) : "–"}</span>
          </div>
        </div>
      ))}
    </section>
  );
}

const diff = (r: OverviewRow) => (r.return != null && r.bench?.SPY != null ? r.return - r.bench.SPY : null);

const Kv = ({ k, v }: { k: string; v: React.ReactNode }) => (
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

