import { X } from "@phosphor-icons/react";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Avatar } from "../components/Avatar";
import { ChartSkeleton, InlineLegend, SeriesChart } from "../components/chart/SeriesChart";
import { PortfolioChart } from "../components/PortfolioChart";
import { StatsTable } from "../components/StatsTable";
import { SymbolSearch } from "../components/SymbolSearch";
import { EmptyState, ErrorLine, Segmented, Signed } from "../components/ui";
import { api, ApiError, type Custom, type Swap, type SymbolItem } from "../lib/api";
import { ccySymbol, compactMoney, dateLong, fullMonthYear, money, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { useApp } from "../state";

export default function WhatIf() {
  const [mode, setMode] = useState<"swap" | "custom">("swap");
  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <h1 className="text-xl font-semibold">What-if</h1>
      <p className="mt-1 max-w-[62ch] text-sm text-muted">Replay your own money through a different choice. Same dates, same amounts, same engine as your real numbers.</p>
      <div className="mt-6">
        <Segmented label="Simulation" value={mode} onChange={setMode} options={[{ value: "swap", label: "Swap one holding" }, { value: "custom", label: "Build a portfolio" }]} />
      </div>
      {mode === "swap" ? <SwapSim /> : <CustomSim />}
    </div>
  );
}

function SwapSim() {
  const txs = useData(api.transactions, "tx");
  const tickers = useMemo(() => Array.from(new Set((txs.data ?? []).filter((t) => t.type === "BUY").map((t) => t.ticker))).sort(), [txs.data]);
  const [ticker, setTicker] = useState("");
  const [rep, setRep] = useState<SymbolItem | null>(null);
  const [res, setRes] = useState<Swap | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = ticker || tickers[0] || "";

  async function run(r: SymbolItem | null = rep) {
    if (!chosen || !r) return;
    setBusy(true); setErr(null);
    try { setRes(await api.swap(chosen, r.symbol)); } catch (e) { setRes(null); setErr(e instanceof ApiError ? e.message : "Couldn't run the simulation."); } finally { setBusy(false); }
  }

  if (txs.data && tickers.length === 0) return <EmptyState title="No purchases to swap yet." body="Add a buy on the Transactions page, then come back to see how a different stock would have done with the same money." />;
  return (
    <section className="mt-10">
      <div className="grid gap-5 md:grid-cols-[220px_1fr_auto] md:items-end">
        <div>
          <label className="label" htmlFor="swap-from">Instead of</label>
          <select id="swap-from" className="field font-mono" value={chosen} onChange={(e) => { setTicker(e.target.value); setRes(null); }}>
            {tickers.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div>
          {rep ? (
            <div>
              <span className="label">I had bought</span>
              <div className="flex h-9 items-center gap-2.5">
                <Avatar ticker={rep.symbol} size={24} /><span className="font-mono text-sm font-semibold">{rep.symbol}</span>
                <span className="truncate text-sm text-muted">{rep.name}</span>
                <button type="button" className="link ml-auto text-sm" onClick={() => { setRep(null); setRes(null); }}>Change</button>
              </div>
            </div>
          ) : <SymbolSearch label="I had bought" exclude={[chosen]} onSelect={(s) => { setRep(s); run(s); }} />}
        </div>
        <button type="button" className="btn-primary" disabled={!rep || busy} onClick={() => run()}>{busy ? "Running" : "Compare"}</button>
      </div>
      {err && <ErrorLine message={err} />}
      {busy && !res && <div className="mt-10"><ChartSkeleton /></div>}
      {res && <SwapResult r={res} />}
    </section>
  );
}

function SwapResult({ r }: { r: Swap }) {
  const better = r.diff > 0;
  const same = Math.abs(r.diff) < 1;
  return (
    <div className="fade-in mt-12" key={`${r.ticker}-${r.replacement}`}>
      <h2 className="max-w-[32ch] text-2xl font-medium md:text-3xl">
        {same ? <>Buying {r.replacement} instead of {r.ticker} would have come out about even.</> : (
          <>Buying <span className="font-mono font-semibold">{r.replacement}</span> instead of <span className="font-mono font-semibold">{r.ticker}</span> would have left you{" "}
            <b className="font-semibold">{compactMoney(Math.abs(r.diff), r.base)}</b> {better ? "richer" : "poorer"}.</>
        )}
      </h2>
      <p className="mt-4 text-sm text-muted">
        {r.trades} trade{r.trades === 1 ? "" : "s"} since {dateLong(r.since)} replayed with the same cash. Your portfolio today{" "}
        <span className="num text-text">{money(r.real_value, r.base, { decimals: 0 })}</span>, with the swap{" "}
        <span className="num text-text">{money(r.hypo_value, r.base, { decimals: 0 })}</span>{" "}
        (<Signed value={r.diff_pct}>{pct(r.diff_pct, 1)}</Signed>).
      </p>
      <div className="mt-8">
        <InlineLegend items={[
          { key: "real", label: "Your portfolio", color: "var(--accent)", width: 2, value: money(r.real_value, r.base, { decimals: 0 }) },
          { key: "hypo", label: `With ${r.replacement}`, color: "var(--series-qqq)", dash: true, value: money(r.hypo_value, r.base, { decimals: 0 }) },
        ]} />
        <div className="mt-3">
          <SeriesChart ariaLabel="Real versus hypothetical portfolio value" dates={r.series.dates} height={320}
            series={[
              { key: "portfolio", label: "Your portfolio", color: "var(--accent)", width: 2, values: r.series.real },
              { key: "hypo", label: `With ${r.replacement}`, color: "var(--series-qqq)", width: 1.5, dash: "5 3", values: r.series.hypo },
            ]}
            format={(v) => money(v, r.base, { decimals: 0 })} short={(v) => compactMoney(v, r.base)} />
        </div>
      </div>
    </div>
  );
}

interface Pick { item: SymbolItem; weight: string }

function CustomSim() {
  const { settings } = useApp();
  const base = settings?.base_currency ?? "ILS";
  const [picks, setPicks] = useState<Pick[]>([]);
  const [start, setStart] = useState(() => { const d = new Date(); d.setFullYear(d.getFullYear() - 5); return d.toISOString().slice(0, 10); });
  const [initial, setInitial] = useState("10000");
  const [monthly, setMonthly] = useState("0");
  const [rebalance, setRebalance] = useState<"none" | "yearly">("none");
  const [res, setRes] = useState<Custom | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const total = picks.reduce((s, p) => s + (Number(p.weight) || 0), 0);

  function add(s: SymbolItem) {
    if (picks.some((p) => p.item.symbol === s.symbol)) return;
    const n = picks.length + 1;
    const even = Math.floor(100 / n);
    setPicks([...picks.map((p) => ({ ...p, weight: String(even) })), { item: s, weight: String(100 - even * (n - 1)) }]);
  }

  async function run() {
    setErr(null); setErrs({});
    if (!picks.length) { setErr("Add at least one symbol."); return; }
    if (Math.abs(total - 100) > 0.01) { setErr(`Weights add up to ${total}%. Make them add up to 100%.`); return; }
    if (!(Number(initial) > 0)) { setErr("Enter a starting amount above 0."); return; }
    setBusy(true);
    try {
      const r = await api.custom({ holdings: picks.map((p) => ({ symbol: p.item.symbol, weight: Number(p.weight) })), start, initial: Number(initial), monthly: Number(monthly) || 0, rebalance });
      if (!r.ok) { setRes(null); setErrs(Object.fromEntries((r.errors ?? []).map((e) => [e.symbol, e.message]))); }
      else setRes(r);
    } catch (e) { setRes(null); setErr(e instanceof ApiError ? e.message : "Couldn't run the simulation."); }
    finally { setBusy(false); }
  }

  return (
    <section className="mt-10">
      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <SymbolSearch label="Add a symbol" exclude={picks.map((p) => p.item.symbol)} onSelect={add} placeholder="SPY, QQQ, a stock" />
          <ul className="mt-4">
            {picks.map((p, i) => (
              <li key={p.item.symbol} className="border-b border-line py-2.5">
                <div className="flex items-center gap-3">
                  <Avatar ticker={p.item.symbol} size={24} />
                  <span className="font-mono text-sm font-semibold">{p.item.symbol}</span>
                  <span className="min-w-0 flex-1 truncate text-sm text-muted">{p.item.name}</span>
                  <label className="sr-only" htmlFor={`w-${i}`}>{p.item.symbol} weight</label>
                  <div className="relative w-20">
                    <input id={`w-${i}`} inputMode="decimal" className="field num h-8 pr-6 text-right text-sm" value={p.weight}
                      onChange={(e) => setPicks(picks.map((x, j) => (j === i ? { ...x, weight: e.target.value } : x)))} />
                    <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-xs text-faint">%</span>
                  </div>
                  <button type="button" aria-label={`Remove ${p.item.symbol}`} className="btn-ghost h-8 w-8 px-0" onClick={() => setPicks(picks.filter((_, j) => j !== i))}><X size={14} /></button>
                </div>
                {errs[p.item.symbol] && <p className="error-text" role="alert">{errs[p.item.symbol]}</p>}
              </li>
            ))}
            {picks.length === 0 && <li className="py-3 text-sm text-faint">Add symbols and weights, for example 60% QQQ and 40% SPY.</li>}
          </ul>
          {picks.length > 0 && <p className={clsx("num mt-2 text-right text-xs", Math.abs(total - 100) > 0.01 ? "text-loss" : "text-muted")}>Total {total}%</p>}
        </div>
        <div className="grid content-start gap-5 sm:grid-cols-2">
          <div><label className="label" htmlFor="c-start">Start date</label><input id="c-start" type="date" className="field" value={start} onChange={(e) => setStart(e.target.value)} /></div>
          <div><label className="label" htmlFor="c-init">Starting amount</label>
            <div className="relative"><span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-faint">{ccySymbol(base)}</span>
              <input id="c-init" inputMode="decimal" className="field num pl-7" value={initial} onChange={(e) => setInitial(e.target.value)} /></div></div>
          <div><label className="label" htmlFor="c-mon">Monthly contribution</label>
            <div className="relative"><span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-faint">{ccySymbol(base)}</span>
              <input id="c-mon" inputMode="decimal" className="field num pl-7" value={monthly} onChange={(e) => setMonthly(e.target.value)} /></div>
            <p className="helper">Added on the first trading day of each month.</p></div>
          <div><span className="label">Rebalancing</span>
            <Segmented label="Rebalancing" value={rebalance} onChange={setRebalance} options={[{ value: "none", label: "None" }, { value: "yearly", label: "Yearly" }]} />
            <p className="helper">Yearly resets weights each January.</p></div>
          <div className="sm:col-span-2">
            <button type="button" className="btn-primary" onClick={run} disabled={busy}>{busy ? "Running" : "Run simulation"}</button>
            {err && <p className="error-text" role="alert">{err}</p>}
          </div>
        </div>
      </div>
      {busy && !res && <div className="mt-12"><ChartSkeleton height={340} /></div>}
      {res && <CustomResult r={res} />}
    </section>
  );
}

function CustomResult({ r }: { r: Custom }) {
  const comps = Object.entries(r.shadow);
  return (
    <div className="fade-in mt-14">
      <h2 className="max-w-[34ch] text-2xl font-medium md:text-3xl">
        This mix would be worth <b className="font-semibold">{compactMoney(r.value, r.base)}</b> today from {compactMoney(r.invested, r.base)} put in since {fullMonthYear(r.range.start)}.
      </h2>
      <p className="mt-4 text-sm text-muted">
        Time-weighted <Signed value={r.twr}>{pct(r.twr, 1)}</Signed>
        {r.xirr !== null && <> · XIRR <Signed value={r.xirr}>{pct(r.xirr, 1)}</Signed></>}
        {comps.map(([k, v]) => <span key={k}> · Same money in {k} <span className="num text-text">{money(v, r.base, { decimals: 0 })}</span></span>)}
      </p>
      <div className="mt-10">
        <PortfolioChart series={r.series} base={r.base} animateKey={r.range.start} labels={{ custom: "This mix", portfolio: "You" }} valueLabel="This mix" valueColor="var(--series-4)" />
      </div>
      <div className="mt-12">
        <StatsTable columns={r.columns} stats={r.stats} labels={{ custom: "This mix", portfolio: "You" }} />
      </div>
    </div>
  );
}
