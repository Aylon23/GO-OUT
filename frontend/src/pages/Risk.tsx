import clsx from "clsx";
import { RangeSelector } from "../components/RangeSelector";
import { StatsTable } from "../components/StatsTable";
import { EmptyState, ErrorLine, InfoTip, Signed, Skel } from "../components/ui";
import { api, type Risk as R } from "../lib/api";
import { dateLong, monthName, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { useApp } from "../state";

export default function Risk() {
  const { range } = useApp();
  const key = JSON.stringify(range);
  const q = useData(() => api.risk(range), `r-${key}`);
  const real = q.data;
  const short = !!real && !real.empty && !!real.range.short_history;
  const d: R | null = real;
  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Risk and stats</h1>
        <RangeSelector />
      </div>
      {q.error && !real ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <RiskSkeleton /> : short ? (
        <EmptyState title="Not enough real history yet."
          body={`Risk figures need at least a month of real results. This portfolio is tracked since ${dateLong(real!.range.tracking_since!)}. Import the transaction history to see earlier years right away.`} />
      ) : d.empty ? (
        <EmptyState title="Nothing to measure yet." body="Once you add transactions, this page compares your risk with SPY and QQQ: drawdowns, volatility, beta and yearly returns." />
      ) : (
        <div className={clsx("fade-in transition-opacity duration", q.loading && "opacity-60")} key={key}>
          <Verdict d={d} />

          <section className="mt-10" aria-label="Statistics">
            <StatsTable columns={d.columns} stats={d.stats} />
            <p className="mt-2 text-xs text-faint">Risk-free rate {pct(d.risk_free, 1, false)} (change it in Settings). Daily data in your base currency.</p>
          </section>
          <div className="mt-16 grid gap-16 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
            <Years d={d} />
            <Heatmap d={d} />
          </div>
        </div>
      )}
    </div>
  );
}

function Verdict({ d }: { d: R }) {
  const me = d.stats.portfolio;
  const ref = d.stats.SPY ? "SPY" : d.columns[1];
  const spy = ref ? d.stats[ref] : undefined;
  if (!me || !spy || me.volatility === null || spy.volatility === null) return null;
  const rel = me.volatility / spy.volatility;
  const swing = rel > 1.1 ? `${((rel - 1) * 100).toFixed(0)}% bumpier than` : rel < 0.9 ? `${((1 - rel) * 100).toFixed(0)}% calmer than` : "about as bumpy as";
  return (
    <p className="mt-8 max-w-[60ch] text-lg">
      Your portfolio has been {swing} {ref}, with a worst drop of{" "}
      <span className="num font-semibold">{pct(me.max_drawdown.value, 1)}</span> against <span className="num">{pct(spy.max_drawdown.value, 1)}</span>.
    </p>
  );
}

function Years({ d }: { d: R }) {
  const label = (k: string) => (k === "portfolio" ? "You" : k);
  return (
    <section aria-labelledby="years-h">
      <h2 id="years-h" className="text-lg font-semibold">Calendar years</h2>
      <p className="mt-1 text-xs text-faint">First and last years are partial.</p>
      <table className="mt-4 w-full text-sm">
        <thead><tr className="border-b border-line text-xs text-muted"><th scope="col" className="py-2 text-left font-medium">Year</th>{d.columns.map((c) => <th key={c} scope="col" className="py-2 text-right font-medium">{label(c)}</th>)}</tr></thead>
        <tbody>
          {[...d.years].reverse().map((y) => (
            <tr key={y.year} className="border-b border-line">
              <th scope="row" className="num py-2 text-left font-normal text-muted">{y.year}</th>
              {d.columns.map((c) => <td key={c} className={clsx("py-2 text-right", c === "portfolio" && "font-medium")}><Signed value={y[c] as number | null}>{pct(y[c] as number | null, 1)}</Signed></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function cellColor(v: number | null) {
  if (v === null) return "transparent";
  const t = Math.min(Math.abs(v) / 0.08, 1);
  const to = v >= 0 ? "var(--gain)" : "var(--loss)";
  return `color-mix(in oklch, ${to} ${Math.round(t * 55)}%, var(--sunken))`;
}

function Heatmap({ d }: { d: R }) {
  const years = Array.from(new Set(Object.keys(d.monthly).map((k) => Number(k.slice(0, 4))))).sort((a, b) => b - a);
  return (
    <section aria-labelledby="heat-h">
      <h2 id="heat-h" className="flex items-center gap-2 text-lg font-semibold">
        <InfoTip text="Your time-weighted return for each month. Darker green is a better month, darker red a worse one; the scale tops out at 8%.">Monthly returns</InfoTip>
      </h2>
      <p className="mt-1 text-xs text-faint">Your portfolio only.</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[560px] border-separate border-spacing-[3px] text-xs">
          <thead><tr><th className="sr-only">Year</th>{Array.from({ length: 12 }, (_, i) => <th key={i} scope="col" className="pb-1 text-center font-medium text-muted">{monthName(i + 1)}</th>)}</tr></thead>
          <tbody>
            {years.map((y) => (
              <tr key={y}>
                <th scope="row" className="num pr-2 text-left font-normal text-muted">{y}</th>
                {Array.from({ length: 12 }, (_, i) => {
                  const k = `${y}-${String(i + 1).padStart(2, "0")}`;
                  const v = d.monthly[k] ?? null;
                  const strong = v !== null && Math.abs(v) >= 0.055;
                  return (
                    <td key={i} title={v === null ? undefined : `${monthName(i + 1)} ${y}: ${pct(v, 2)}`}
                      className={clsx("num h-8 rounded-[4px] text-center", v === null ? "text-faint" : strong ? "font-medium text-text" : "text-text")}
                      style={{ background: cellColor(v) }}>
                      {v === null ? "" : pct(v, 1).replace("%", "")}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs text-muted" aria-hidden>
        <span>−8%</span>
        <span className="h-2 w-28 rounded-full" style={{ background: "linear-gradient(to right, color-mix(in oklch, var(--loss) 55%, var(--sunken)), var(--sunken), color-mix(in oklch, var(--gain) 55%, var(--sunken)))" }} />
        <span>+8%</span>
      </div>
    </section>
  );
}

function RiskSkeleton() {
  return (
    <div aria-hidden className="mt-8">
      <Skel className="h-7 w-[min(520px,100%)]" />
      <div className="mt-10 space-y-0">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="flex justify-between border-b border-line py-3"><Skel className="h-4 w-32" /><Skel className="h-4 w-48" /></div>)}</div>
    </div>
  );
}
