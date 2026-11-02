import { useState } from "react";
import type { Series } from "../lib/api";
import { compactMoney, money, pct } from "../lib/format";
import { colorFor, InlineLegend, SeriesChart, type SeriesDef } from "./chart/SeriesChart";
import { Segmented } from "./ui";

export type ChartView = "value" | "performance" | "drawdown";

/** Value / Performance / Drawdown chart. Shared by Overview and What-if. */
export function PortfolioChart({ series, base, labels = {}, animateKey, rangeControl, valueLabel = "Portfolio value", valueColor = "var(--accent)", hideInvested = false, note }: {
  series: Series; base: string; labels?: Record<string, string>; animateKey: string; rangeControl?: React.ReactNode; valueLabel?: string; valueColor?: string; hideInvested?: boolean; note?: string;
}) {
  const [view, setView] = useState<ChartView>("performance");
  const label = (k: string) => labels[k] ?? (k === "portfolio" ? "You" : k);
  const keys = Object.keys(series.perf);
  const color = (k: string, i: number) => colorFor(k, i);
  const width = (k: string) => (k === "portfolio" || k === "custom" ? 2 : 1.5);

  let defs: SeriesDef[];
  let format: (v: number) => string;
  let short: (v: number) => string;
  let end: (v: number) => string;
  let legend: { key: string; label: string; color: string; value?: string; width?: number; dash?: boolean }[];
  let baseline: number | undefined;

  if (view === "value") {
    defs = [{ key: "portfolio", label: valueLabel, color: valueColor, width: 2, values: series.value }];
    if (!hideInvested) defs.push({ key: "invested", label: "Net deposits", color: "var(--series-spy)", width: 1.5, dash: "4 3", step: true, values: series.invested });
    format = (v) => money(v, base, { decimals: 0 });
    short = (v) => compactMoney(v, base);
    end = short;
    const lv = series.value[series.value.length - 1];
    const li = series.invested[series.invested.length - 1];
    legend = [{ key: "v", label: valueLabel, color: valueColor, width: 2, value: lv != null ? money(lv, base, { decimals: 0 }) : undefined }];
    if (!hideInvested) legend.push({ key: "i", label: "Net deposits", color: "var(--series-spy)", dash: true, value: li != null ? money(li, base, { decimals: 0 }) : undefined });
  } else if (view === "performance") {
    defs = keys.map((k, i) => ({ key: k, label: label(k), color: color(k, i), width: width(k), values: series.perf[k] }));
    format = (v) => `${v.toFixed(1)}  (${pct(v / 100 - 1, 1)})`;
    short = (v) => pct(v / 100 - 1, 0);
    end = (v) => pct(v / 100 - 1, 1);
    baseline = 100;
    legend = defs.map((d, i) => { const l = d.values[d.values.length - 1]; return { key: d.key, label: d.label, color: color(d.key, i), width: width(d.key), value: l != null ? pct(l / 100 - 1, 1) : undefined }; });
  } else {
    defs = keys.map((k, i) => ({ key: k, label: label(k), color: color(k, i), width: width(k), values: series.drawdown[k] }));
    format = (v) => pct(v / 100, 1);
    short = (v) => pct(v / 100, 0);
    end = (v) => pct(v / 100, 1);
    baseline = 0;
    legend = defs.map((d, i) => {
      const worst = Math.min(...(d.values.filter((x) => x != null) as number[]));
      return { key: d.key, label: `${d.label} worst`, color: color(d.key, i), width: width(d.key), value: isFinite(worst) ? pct(worst / 100, 1) : undefined };
    });
  }

  const desc = view === "value" ? "Portfolio value over time with net deposits" : view === "performance" ? "Time-weighted performance rebased to 100" : "Drop from previous peak";
  return (
    <div>
      <div className="flex flex-col-reverse gap-3 md:flex-row md:items-center md:justify-between">
        <Segmented label="Chart" value={view} onChange={setView}
          options={[{ value: "value", label: "Value" }, { value: "performance", label: "Performance" }, { value: "drawdown", label: "Drawdown" }]} />
        {rangeControl}
      </div>
      <div className="mt-5 min-h-[24px]"><InlineLegend items={legend} /></div>
      <div className="mt-3">
        {series.dates.length < 2 ? (
          <div className="flex h-[200px] items-center justify-center rounded-md border border-dashed border-line text-sm text-muted">
            The chart fills in as trading days pass.
          </div>
        ) : <SeriesChart ariaLabel={desc} dates={series.dates} series={defs} format={format} short={short} end={end} baseline={baseline} animateKey={`${animateKey}-${view}`} height={340} />}
      </div>
      <p className="mt-2 text-xs text-faint">
        {note ?? (view === "performance" ? `Time-weighted, so deposits and withdrawals don't move the line. Benchmarks include dividends. All in ${base === "USD" ? "dollars" : "shekels"}.`
          : view === "drawdown" ? "How far each line sat below its previous high."
          : "Net deposits step up when money goes in and down when it comes out.")}
      </p>
    </div>
  );
}
