import { UploadSimple } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { ChartSkeleton } from "../components/chart/SeriesChart";
import { PortfolioChart } from "../components/PortfolioChart";
import { RangeSelector } from "../components/RangeSelector";
import { SymbolSearch } from "../components/SymbolSearch";
import { ErrorLine, InfoTip, Signed, Skel } from "../components/ui";
import { api, ApiError, type Portfolio } from "../lib/api";
import { compactMoney, dateLong, fullMonthYear, money, pct } from "../lib/format";
import { invalidate, useData } from "../lib/hooks";
import { useApp } from "../state";

export function rangePhrase(code: string, start: string) {
  switch (code) {
    case "1M": return "over the past month";
    case "3M": return "over the past 3 months";
    case "YTD": return "so far this year";
    case "1Y": return "over the past year";
    case "3Y": return "over the past 3 years";
    case "5Y": return "over the past 5 years";
    case "MAX": return `since ${fullMonthYear(start)}`;
    default: return `since ${dateLong(start)}`;
  }
}

function Headline({ p }: { p: Portfolio }) {
  const s = p.summary;
  const up = s.twr >= 0;
  const start = p.range.code === "MAX" ? p.range.first_date : p.range.start;
  const comps = s.benchmarks.filter((b) => b.twr !== null).map((b) => {
    const diff = (s.twr - (b.twr as number)) * 100;
    if (Math.abs(diff) < 0.05) return <span key={b.symbol}>level with {b.symbol}</span>;
    return <span key={b.symbol}><b className="font-semibold">{Math.abs(diff).toFixed(1)} points</b> {diff > 0 ? "ahead of" : "behind"} {b.symbol}</span>;
  });
  return (
    <h1 className="max-w-[30ch] text-2xl font-medium md:text-3xl">
      {up ? "Up" : "Down"} <b className="font-semibold">{Math.abs(s.twr * 100).toFixed(1)}%</b> {rangePhrase(p.range.code, start)}.
      {comps.length > 0 && <span className="text-muted"> {comps.reduce<React.ReactNode[]>((acc, c, i) => (i === 0 ? [c] : [...acc, ", ", c]), [])}.</span>}
    </h1>
  );
}

function SecondaryLine({ p }: { p: Portfolio }) {
  const s = p.summary;
  const b = p.base;
  const items: React.ReactNode[] = [
    <span key="v">Value <span className="num font-medium text-text">{money(s.value, b, { decimals: 0 })}</span></span>,
    <span key="g">Total gain <Signed value={s.total_gain} className="font-medium">{money(s.total_gain, b, { signed: true, decimals: 0 })}</Signed></span>,
    <span key="t">Today <Signed value={s.today_change} className="font-medium">{money(s.today_change, b, { signed: true, decimals: 0 })} ({pct(s.today_pct)})</Signed></span>,
    <span key="x" className="inline-flex items-center gap-1">
      <InfoTip text="XIRR: your personal annual return, counting when you added or took out money.">XIRR</InfoTip>
      <Signed value={s.xirr} className="font-medium">{s.xirr === null ? "–" : pct(s.xirr, 1)}</Signed>
    </span>,
  ];
  return (
    <p className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
      {items.map((it, i) => <span key={i} className="inline-flex items-center gap-2">{i > 0 && <span aria-hidden className="text-faint">·</span>}{it}</span>)}
    </p>
  );
}

function ShadowRows({ p }: { p: Portfolio }) {
  const b = p.base;
  const actual = p.shadow.actual;
  const best = p.shadow.rows.length ? p.shadow.rows.reduce((m, r) => (r.value > m.value ? r : m)) : null;
  const beatAll = p.shadow.rows.every((r) => r.diff >= 0);
  return (
    <section aria-labelledby="shadow-h" className="mt-16">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="shadow-h" className="text-lg font-semibold">If you had bought the index instead</h2>
        <InfoTip text="Your exact deposits and withdrawals, on the same dates, put into SPY or QQQ (dividends reinvested). The range start counts as one deposit of what you had then.">
          <span className="text-xs text-faint">How this works</span>
        </InfoTip>
      </div>
      <p className="mt-1 text-sm text-muted">
        {best && (beatAll
          ? <>Your picks are ahead of every index {rangePhrase(p.range.code, p.range.code === "MAX" ? p.range.first_date : p.range.start)}.</>
          : <>{best.symbol} would have left you <span className="num font-medium text-text">{compactMoney(best.value - actual, b)}</span> richer {rangePhrase(p.range.code, p.range.code === "MAX" ? p.range.first_date : p.range.start)}.</>)}
      </p>
      <dl className="mt-5 text-sm">
        <div className="grid grid-cols-[1fr_auto] items-baseline gap-x-6 border-t border-line py-3 md:grid-cols-[1fr_160px_200px]">
          <dt className="font-medium">You</dt>
          <dd className="num text-right font-semibold">{money(actual, b, { decimals: 0 })}</dd>
          <dd className="hidden text-right text-faint md:block">Actual</dd>
        </div>
        {p.shadow.rows.map((r) => (
          <div key={r.symbol} className="grid grid-cols-[1fr_auto] items-baseline gap-x-6 border-t border-line py-3 md:grid-cols-[1fr_160px_200px]">
            <dt className="text-muted">Same deposits in <span className="font-mono font-semibold text-text">{r.symbol}</span></dt>
            <dd className="num text-right">{money(r.value, b, { decimals: 0 })}</dd>
            <dd className="col-span-2 text-right md:col-span-1">
              <Signed value={r.diff}>{r.diff >= 0 ? "You're ahead " : "You're behind "}{money(Math.abs(r.diff), b, { decimals: 0 })} ({pct(r.diff_pct, 1)})</Signed>
            </dd>
          </div>
        ))}
        <div className="border-t border-line" />
      </dl>
    </section>
  );
}

function Empty() {
  const { startAddTx, go } = useApp();
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  return (
    <div className="fade-in mx-auto max-w-[640px] py-[12dvh]">
      <h1 className="text-2xl font-medium md:text-3xl">Are your picks beating the index?</h1>
      <p className="mt-3 text-base text-muted">Add what you bought and when. You'll see your return next to SPY and QQQ, in money and in percent.</p>
      <div className="mt-8">
        <SymbolSearch size="lg" placeholder="Search a stock to add your first purchase" onSelect={(s) => startAddTx(s)} />
      </div>
      <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-muted">
        <button type="button" className="link inline-flex items-center gap-1.5 text-sm" onClick={() => go("transactions")}><UploadSimple size={14} /> Import a CSV</button>
        <button type="button" className="link text-sm" disabled={loading}
          onClick={async () => { setLoading(true); setErr(null); try { await api.demo(); invalidate(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't load the demo."); } finally { setLoading(false); } }}>
          {loading ? "Loading demo portfolio" : "Load demo portfolio"}
        </button>
      </div>
      {err && <ErrorLine message={err} />}
    </div>
  );
}

function DashSkeleton() {
  return (
    <div aria-hidden>
      <Skel className="h-10 w-[min(560px,100%)]" /><Skel className="mt-3 h-10 w-[min(380px,80%)]" />
      <Skel className="mt-5 h-4 w-[min(460px,90%)]" />
      <div className="mt-12 flex justify-between"><Skel className="h-9 w-64" /><Skel className="hidden h-8 w-80 md:block" /></div>
      <div className="mt-8"><ChartSkeleton height={340} /></div>
    </div>
  );
}

export default function Dashboard() {
  const { range } = useApp();
  const key = JSON.stringify(range);
  const q = useData(() => api.portfolio(range), `p-${key}`);
  const p = q.data;
  if (q.error && !p) return <Page><ErrorLine message={q.error.message} onRetry={q.reload} /></Page>;
  if (!p) return <Page><DashSkeleton /></Page>;
  if (p.empty) return <Page><Empty /></Page>;
  return (
    <Page>
      <div className={clsx("transition-opacity duration", q.loading && "opacity-60")}>
        <Headline p={p} />
        <SecondaryLine p={p} />
        {p.warnings.map((w) => <p key={w} className="mt-3 text-sm text-loss">{w}</p>)}
        <section className="mt-12" aria-label="Chart">
          <PortfolioChart series={p.series} base={p.base} animateKey={key} rangeControl={<RangeSelector />} />
        </section>
        <ShadowRows p={p} />
      </div>
    </Page>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-content px-4 pb-24 pt-10 md:px-8 md:pt-16">{children}</div>;
}
