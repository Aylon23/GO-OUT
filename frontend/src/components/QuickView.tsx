import { Eye, EyeSlash, Plus } from "@phosphor-icons/react";
import { api } from "../lib/api";
import { money, pct, signClass } from "../lib/format";
import { useData } from "../lib/hooks";
import { useApp } from "../state";
import { Avatar } from "./Avatar";
import { ChartSkeleton, colorFor, InlineLegend, SeriesChart } from "./chart/SeriesChart";
import { ErrorLine, SidePanel, Skel } from "./ui";

export function QuickView() {
  const { quickView: s, openQuickView, startAddTx } = useApp();
  const sym = s?.symbol ?? "";
  const chart = useData(() => api.symbolChart(sym, "1Y"), `qv-${sym}`, !!s);
  const quote = useData(() => api.quote(sym), `qvq-${sym}`, !!s);
  const watch = useData(api.watchlist, "watchlist", !!s);
  const watching = !!watch.data?.includes(sym);
  if (!s) return null;
  const q = quote.data;
  const c = chart.data;
  const keys = c ? Object.keys(c.series) : [];
  const last = (k: string) => (c ? c.series[k][c.series[k].length - 1] / 100 - 1 : null);
  return (
    <SidePanel open title={s.symbol} onClose={() => openQuickView(null)}>
      <div className="flex items-center gap-3">
        <Avatar ticker={s.symbol} size={36} />
        <div className="min-w-0">
          <div className="truncate font-medium">{s.name}</div>
          <div className="text-xs text-faint">{[s.exchange, s.type, s.currency].filter(Boolean).join(" · ")}</div>
        </div>
      </div>
      <div className="mt-6 flex items-baseline gap-3">
        {q ? (
          <>
            <span className="num text-xl font-semibold">{money(q.price, q.currency, { decimals: 2 })}</span>
            <span className={`num text-sm ${signClass(q.change_pct)}`}>{pct(q.change_pct)} today</span>
          </>
        ) : quote.error ? <span className="text-sm text-muted">{quote.error.message}</span> : <Skel className="h-8 w-40" />}
      </div>
      <div className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-medium">1 year vs SPY</h3>
          <span className="text-xs text-faint">Total return in its own currency</span>
        </div>
        {c && (
          <InlineLegend items={keys.map((k, i) => ({ key: k, label: k, color: i === 0 ? "var(--accent)" : colorFor(k), width: i === 0 ? 2 : 1.5, value: pct(last(k), 1) }))} />
        )}
        <div className="mt-2">
          {chart.error ? <ErrorLine message={chart.error.message} onRetry={chart.reload} /> : !c ? <ChartSkeleton height={220} /> : (
            <SeriesChart ariaLabel={`${s.symbol} versus SPY over one year`} height={220} dates={c.dates} baseline={100}
              series={keys.map((k, i) => ({ key: i === 0 ? "portfolio" : k, label: k, color: i === 0 ? "var(--accent)" : colorFor(k), width: i === 0 ? 2 : 1.5, values: c.series[k] }))}
              format={(v) => pct(v / 100 - 1, 1)} short={(v) => pct(v / 100 - 1, 0)} end={(v) => pct(v / 100 - 1, 1)} />
          )}
        </div>
      </div>
      <button type="button" className="btn-primary mt-8 w-full" onClick={() => startAddTx(s)}>
        <Plus size={16} /> Add transaction
      </button>
      <button type="button" className="btn-secondary mt-2 w-full" onClick={async () => { await (watching ? api.unwatch(sym) : api.watch(sym)); watch.reload(); }}>
        {watching ? <><EyeSlash size={16} /> Stop watching</> : <><Eye size={16} /> Watch</>}
      </button>
    </SidePanel>
  );
}
