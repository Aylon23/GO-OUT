import clsx from "clsx";
import type { Stat } from "../lib/api";
import { dateLong, pct, ratio } from "../lib/format";
import { colorFor } from "./chart/SeriesChart";
import { InfoTip, Signed } from "./ui";

const ROWS: { key: string; label: string; tip: string; render: (s: Stat) => React.ReactNode }[] = [
  { key: "cagr", label: "CAGR", tip: "Compound annual growth rate: the steady yearly return that gets you from start to end. Not annualized for ranges under a year.", render: (s) => <Signed value={s.cagr}>{pct(s.cagr, 1)}</Signed> },
  { key: "vol", label: "Volatility", tip: "How much daily returns swing, scaled to a year. Higher means a bumpier ride.", render: (s) => <span className="num">{pct(s.volatility, 1, false)}</span> },
  { key: "mdd", label: "Max drawdown", tip: "The worst drop from a peak before recovering.", render: (s) => (
    <span className="block">
      <Signed value={s.max_drawdown.value}>{pct(s.max_drawdown.value, 1)}</Signed>
      {s.max_drawdown.peak && <span className="num block text-xs text-faint">{dateLong(s.max_drawdown.peak)} to {dateLong(s.max_drawdown.trough!)}{s.max_drawdown.recovery ? "" : ", not yet recovered"}</span>}
    </span>) },
  { key: "sharpe", label: "Sharpe ratio", tip: "Return above the risk-free rate per unit of volatility. Above 1 is good, above 2 is rare.", render: (s) => <span className="num">{ratio(s.sharpe)}</span> },
  { key: "beta", label: "Beta vs SPY", tip: "How much this moves when SPY moves 1%. 1.2 means about 1.2% on average.", render: (s) => <span className="num">{ratio(s.beta)}</span> },
  { key: "corr", label: "Correlation vs SPY", tip: "How closely daily moves follow SPY, from −1 (opposite) to 1 (in lockstep).", render: (s) => <span className="num">{ratio(s.correlation)}</span> },
  { key: "best", label: "Best year", tip: "Highest calendar-year return in the range (partial years included).", render: (s) => s.best_year ? <span><Signed value={s.best_year.value}>{pct(s.best_year.value, 1)}</Signed> <span className="num text-xs text-faint">{s.best_year.year}</span></span> : "–" },
  { key: "worst", label: "Worst year", tip: "Lowest calendar-year return in the range (partial years included).", render: (s) => s.worst_year ? <span><Signed value={s.worst_year.value}>{pct(s.worst_year.value, 1)}</Signed> <span className="num text-xs text-faint">{s.worst_year.year}</span></span> : "–" },
];

export function StatsTable({ columns, stats, labels = {} }: { columns: string[]; stats: Record<string, Stat>; labels?: Record<string, string> }) {
  const label = (k: string) => labels[k] ?? (k === "portfolio" ? "You" : k);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th scope="col" className="py-2 pr-4 text-left font-medium"><span className="sr-only">Metric</span></th>
            {columns.map((c, i) => (
              <th key={c} scope="col" className="py-2 pl-4 text-right font-medium">
                <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-2.5 rounded-full" style={{ background: colorFor(c, i) }} /><span className={clsx(c === "portfolio" && "text-text")}>{label(c)}</span></span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((r) => (
            <tr key={r.key} className="border-b border-line align-top">
              <th scope="row" className="py-2.5 pr-4 text-left font-normal text-muted"><InfoTip text={r.tip}>{r.label}</InfoTip></th>
              {columns.map((c) => <td key={c} className={clsx("py-2.5 pl-4 text-right", c === "portfolio" && "font-medium")}>{stats[c] ? r.render(stats[c]) : "–"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
