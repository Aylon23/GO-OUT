import { ArrowRight, CaretDown, FloppyDisk, Lightning, Plus, ShieldCheck, Sparkle, X } from "@phosphor-icons/react";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SymbolSearch } from "../components/SymbolSearch";
import { Avatar } from "../components/Avatar";
import { Researched } from "../components/Researched";
import { EmptyState, ErrorLine, InfoTip, Segmented, Signed, Skel } from "../components/ui";
import {
  api, ApiError, type EventReplay, type Idea, type ImpactHolding, type ModelCheck, type Regime, type SavedScenario, type ScenarioLibrary,
  type ScenarioParse, type ScenarioRun, type Shock,
} from "../lib/api";
import { dateLong, money, pct } from "../lib/format";
import { useData, useDebounced } from "../lib/hooks";
import { CustomSim, SwapSim } from "./WhatIf";

const EXAMPLES = ["מלחמה עם איראן", "מחיר הנפט יורד 20%", "תשואות האג״ח בארה״ב עולות", "אפל ידווחו דוחות חלשים", "Nasdaq falls 10%", "The Fed cuts rates"];
const HORIZONS = [{ value: "1", label: "1 week" }, { value: "2", label: "2 weeks" }, { value: "4.3", label: "1 month" }, { value: "13", label: "3 months" }];

export default function Scenarios() {
  const [mode, setMode] = useState<"scenario" | "researched" | "swap" | "custom">("scenario");
  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <h1 className="text-xl font-semibold">What-if</h1>
      <p className="mt-1 max-w-[66ch] text-sm text-muted">
        {mode === "researched" ? "Scenarios researched outside the app and imported, checked against real price history."
          : mode === "scenario" ? "Describe something that could happen. See what it would do to each portfolio, why, and what past events like it did."
          : "Replay your own money through a different choice. Same dates, same amounts, same engine as your real numbers."}
      </p>
      <div className="mt-6">
        <Segmented label="What-if tool" value={mode} onChange={setMode}
          options={[{ value: "scenario", label: "Scenarios" }, { value: "researched", label: "Researched" }, { value: "swap", label: "Swap a holding" }, { value: "custom", label: "Build a portfolio" }]} />
      </div>
      {mode === "scenario" ? <ScenarioLab /> : mode === "researched" ? <Researched /> : mode === "swap" ? <SwapSim /> : <CustomSim />}
    </div>
  );
}

type EditShock = Shock & { key: string };
let keySeq = 0;
const withKey = (s: Shock): EditShock => ({ ...s, key: `${s.symbol}-${++keySeq}` });

function ScenarioLab() {
  const lib = useData(api.scenarioLibrary, "scenario-lib");
  const saved = useData(api.scenarios, "scenarios");
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<ScenarioParse | null>(null);
  const [shocks, setShocks] = useState<EditShock[]>([]);
  const [horizon, setHorizon] = useState(1);
  const [events, setEvents] = useState<string[]>([]);
  const [usedEvents, setUsedEvents] = useState<string[]>([]);
  const [regime, setRegime] = useState<Regime>("normal");
  const [parseErr, setParseErr] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [run, setRun] = useState<ScenarioRun | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [view, setView] = useState<string>("family");

  const read = useCallback(async (t: string) => {
    const q = t.trim();
    if (!q) return;
    setParsing(true); setParseErr(null);
    try {
      const p = await api.scenarioParse(q);
      setParsed(p); setShocks(p.shocks.map(withKey)); setHorizon(p.horizon_weeks || 1); setEvents(p.events); setUsedEvents(p.used_events ?? []); setRegime("normal");
      if (!p.understood) setRun(null);
    } catch (e) { setParseErr(e instanceof ApiError ? e.message : "Couldn't read that."); } finally { setParsing(false); }
  }, []);

  const load = (s: SavedScenario) => {
    setText(s.text); setParsed({ text: s.text, parser: "rules", label: s.name, themes: [], shocks: s.shocks, events: s.events, used_events: s.used_events ?? [], horizon_weeks: s.horizon_weeks, notes: [], understood: true });
    setShocks(s.shocks.map((x) => withKey({ ...x, basis: x.basis ?? "manual" }))); setHorizon(s.horizon_weeks); setEvents(s.events); setUsedEvents(s.used_events ?? []); setRegime("normal");
  };

  // run whenever the shocks change
  const body = useMemo(() => JSON.stringify({ shocks: shocks.filter((s) => isFinite(s.move)).map(({ symbol, kind, move }) => ({ symbol, kind, move })), horizon_weeks: horizon, events, used_events: usedEvents, regime }), [shocks, horizon, events, usedEvents, regime]);
  const debounced = useDebounced(body, 350);
  useEffect(() => {
    const b = JSON.parse(debounced);
    if (!b.shocks.length) { setRun(null); return; }
    const ctl = new AbortController();
    setRunning(true); setRunErr(null);
    api.scenarioRun(b, ctl.signal).then((r) => { setRun(r); setRunning(false); })
      .catch((e) => { if (e.name !== "AbortError") { setRunErr(e instanceof ApiError ? e.message : "Couldn't run the scenario."); setRunning(false); } });
    return () => ctl.abort();
  }, [debounced]);

  const ccy = run?.base_currency ?? "USD";
  const firstLoad = running && !run;
  return (
    <section className="mt-8">
      <form className="relative" onSubmit={(e) => { e.preventDefault(); read(text); }}>
        <label htmlFor="scenario-text" className="sr-only">Describe a scenario</label>
        <Lightning size={20} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-accent" />
        <input id="scenario-text" dir="auto" autoComplete="off" className="field h-12 pl-12 pr-28 text-base" value={text} onChange={(e) => setText(e.target.value)}
          placeholder="What if… oil jumps 20%, war with Iran, Apple misses earnings" />
        <button type="submit" className="btn-primary absolute right-1.5 top-1/2 h-9 -translate-y-1/2" disabled={!text.trim() || parsing}>
          {parsing ? "Reading" : "Run"}<ArrowRight size={14} />
        </button>
      </form>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {EXAMPLES.map((x) => (
          <button key={x} type="button" dir="auto" className="btn-ghost h-7 rounded-full border border-line px-3 text-xs" onClick={() => { setText(x); read(x); }}>{x}</button>
        ))}
      </div>
      {saved.data && saved.data.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs text-faint">Saved:</span>
          {saved.data.map((s) => (
            <span key={s.id} className="flex h-7 items-center rounded-full border border-line-strong pl-3 pr-1 text-xs">
              <button type="button" dir="auto" className="press hover:text-accent" onClick={() => load(s)}>{s.name}</button>
              <button type="button" aria-label={`Delete ${s.name}`} className="btn-ghost h-5 w-5 px-0" onClick={async () => { await api.deleteScenario(s.id); saved.reload(); }}><X size={10} /></button>
            </span>
          ))}
        </div>
      )}
      {parseErr && <ErrorLine message={parseErr} />}

      {parsed && (
        <div className="fade-in mt-8">
          <ReadAs parsed={parsed} />
          {!parsed.understood && shocks.length === 0 ? (
            <EmptyState title="I couldn't turn that into market moves."
              body="Name something that moves prices: oil, rates or bonds, the dollar or shekel, a sector, an index, a company, or an event like a war, a recession or tariffs. Or add a shock by hand below." />
          ) : null}
          <ShockEditor shocks={shocks} setShocks={setShocks} lib={lib.data} run={run} horizon={horizon} setHorizon={setHorizon} />
          {shocks.length > 0 && <SaveBar text={text} shocks={shocks} events={events} usedEvents={usedEvents} horizon={horizon} defaultName={parsed.label || text} onSaved={saved.reload} />}
        </div>
      )}
      {!parsed && shocks.length === 0 && (
        <div className="mt-10 max-w-[66ch] text-sm text-muted">
          <p>How it works: your sentence becomes a few moves in market factors (oil, rates, the dollar, sectors) or in one company. When you don't give a size, it uses a strong but ordinary week from history, or what past events of the same kind did. Everything else moves the way it has moved alongside those factors over the last ten years.</p>
          <p className="mt-2">You can edit every move before trusting the result.</p>
        </div>
      )}

      {runErr && <ErrorLine message={runErr} />}
      {firstLoad && <div className="mt-10 space-y-3" aria-label="Loading"><Skel className="h-10 w-2/3" /><Skel className="h-4 w-1/2" /><Skel className="h-40 w-full" />
        <p className="text-xs text-faint">The first run loads ten years of market history. It can take a minute; later runs are instant.</p></div>}
      {run && !run.empty && (
        <div className={clsx("transition-opacity duration-fast", running && "opacity-60")}>
          {run.warnings.map((w) => <p key={w} className="mt-2 text-xs text-faint">{w}</p>)}
          <FamilySummary run={run} ccy={ccy} view={view} setView={setView} setRegime={setRegime} />
          <Market run={run} />
          <HoldingsImpact run={run} view={view} ccy={ccy} />
          <Protection run={run} view={view} ccy={ccy} />
          <PastEvents events={run.events} ccy={ccy} />
          <Reverse run={run} />
          <ModelCheckPanel />
          <p className="mt-12 max-w-[72ch] text-xs text-faint">
            Estimates from how prices moved together in the past ({run.weeks} weeks; {run.stress_weeks} sell-off weeks), not a forecast. Linear, no options, nothing outside past experience.
            Protection ideas are things to weigh, not advice. Method: docs/SCENARIOS.md.
          </p>
        </div>
      )}
    </section>
  );
}

function ReadAs({ parsed }: { parsed: ScenarioParse }) {
  return (
    <div className="text-sm">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted">
        {parsed.parser === "ai" ? <><Sparkle size={14} className="text-accent" />Read by Claude</> : <>Read by the local rules</>}
        {parsed.themes.length > 0 && <span>· {parsed.themes.map((t) => t.label).join(", ")}</span>}
        {parsed.label && !parsed.themes.length && parsed.parser === "ai" && <span dir="auto">· {parsed.label}</span>}
      </p>
      {parsed.parser === "rules" && <p className="mt-1 text-xs text-faint">Free-text research coming soon. Local rules understand simple moves like “oil falls 20%”.</p>}
      {parsed.notes.map((n) => <p key={n} className="mt-1 text-xs text-muted">{n}</p>)}
    </div>
  );
}

function plausibilityText(p: ScenarioRun["plausibility"]) {
  if (p.seen === 0) return `Not seen in ${Math.round((p.weeks ?? 520) / 52)} years of data. Treat the numbers as a rough guide.`;
  if (p.seen && p.weeks) {
    const yrs = Math.round(p.weeks / 52);
    if (p.seen > p.weeks / 4) return `Common: weeks at least this unusual happen most months.`;
    return `${p.label[0].toUpperCase()}${p.label.slice(1)}: ${p.seen} week${p.seen === 1 ? "" : "s"} at least this unusual in the last ${yrs} years.`;
  }
  return p.label;
}

function ShockEditor({ shocks, setShocks, lib, run, horizon, setHorizon }: {
  shocks: EditShock[]; setShocks: (f: (s: EditShock[]) => EditShock[]) => void; lib: ScenarioLibrary | null; run: ScenarioRun | null;
  horizon: number; setHorizon: (h: number) => void;
}) {
  const [adding, setAdding] = useState(false);
  const z = (sym: string) => run?.shocks.find((s) => s.symbol === sym)?.z ?? null;
  const groups = useMemo(() => {
    const g: Record<string, ScenarioLibrary["factors"]> = {};
    for (const f of lib?.factors ?? []) (g[f.group] ??= []).push(f);
    return g;
  }, [lib]);
  const add = (s: Shock) => { setShocks((xs) => xs.some((x) => x.symbol === s.symbol) ? xs : [...xs, withKey(s)]); setAdding(false); };
  const horizonValue = HORIZONS.reduce((best, h) => Math.abs(Number(h.value) - horizon) < Math.abs(Number(best.value) - horizon) ? h : best, HORIZONS[0]).value;
  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-base font-semibold">The moves</h2>
        <label className="flex items-center gap-2 text-sm text-muted">Over
          <select className="field h-8 w-auto py-0 text-sm" value={horizonValue} onChange={(e) => setHorizon(Number(e.target.value))}>
            {HORIZONS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
          </select>
        </label>
      </div>
      <ul className="mt-3 border-t border-line">
        {shocks.map((s) => (
          <li key={s.key} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 gap-y-1 border-b border-line py-2.5 sm:grid-cols-[minmax(0,1.3fr)_auto_minmax(0,1.5fr)_auto]">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{s.label ?? s.symbol}</p>
              <p className="font-mono text-xs text-faint">{s.symbol}{s.kind === "stock" && " · company news"}</p>
            </div>
            <MoveInput value={s.move} onChange={(v) => setShocks((xs) => xs.map((x) => x.key === s.key ? { ...x, move: v, basis: "manual", basis_text: "Set by you" } : x))} label={`Move for ${s.symbol}`} />
            <p className="col-span-3 text-xs text-muted sm:col-span-1">
              {s.split && s.basis !== "manual" && <span className="font-medium text-text">Past events split: no clear direction. </span>}
              {s.basis_text}
              {z(s.symbol) != null && <span className="text-faint"> · {Math.abs(z(s.symbol)!).toFixed(1)}× a normal week</span>}
            </p>
            <button type="button" aria-label={`Remove ${s.symbol}`} className="btn-ghost col-start-3 row-start-1 h-8 w-8 px-0 sm:col-start-4" onClick={() => setShocks((xs) => xs.filter((x) => x.key !== s.key))}><X size={14} /></button>
          </li>
        ))}
      </ul>
      {run?.pinned && <p className="mt-2 text-xs text-faint">Company news only: the overall market (S&P 500) is held still, so the result shows what is tied to the company itself.</p>}
      {run && !run.empty && <p className="mt-2 text-xs text-muted">{plausibilityText(run.plausibility)}</p>}
      {adding ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <select aria-label="Add a market factor" className="field" defaultValue="" onChange={(e) => {
            const f = lib?.factors.find((x) => x.symbol === e.target.value);
            if (f) add({ symbol: f.symbol, kind: "factor", move: -0.05, label: f.label, basis: "manual", basis_text: "Set by you" });
          }}>
            <option value="" disabled>Add a market factor…</option>
            {Object.entries(groups).map(([g, fs]) => <optgroup key={g} label={g}>{fs.map((f) => <option key={f.symbol} value={f.symbol}>{f.label}</option>)}</optgroup>)}
          </select>
          <SymbolSearch placeholder="…or a company" onSelect={(it) => add({ symbol: it.symbol, kind: "stock", move: -0.1, label: it.name, basis: "manual", basis_text: "Set by you" })} />
        </div>
      ) : (
        <button type="button" className="btn-ghost mt-2 -ml-2" onClick={() => setAdding(true)}><Plus size={14} />Add a move</button>
      )}
    </div>
  );
}

function MoveInput({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value * 100).toFixed(1);
  const commit = () => {
    if (draft === null) return;
    const v = Number(draft.replace("−", "-").replace(",", "."));
    if (isFinite(v) && v > -95 && v < 500) onChange(v / 100);
    setDraft(null);
  };
  return (
    <div className="relative w-24">
      <input aria-label={label} inputMode="decimal" className={clsx("field num h-8 pr-6 text-right text-sm", value < 0 ? "text-loss" : value > 0 ? "text-gain" : "")}
        value={shown.startsWith("-") || draft !== null || value <= 0 ? shown : `+${shown}`} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-faint">%</span>
    </div>
  );
}

function SaveBar({ text, shocks, events, usedEvents, horizon, defaultName, onSaved }: { text: string; shocks: EditShock[]; events: string[]; usedEvents: string[]; horizon: number; defaultName: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(defaultName);
  const [done, setDone] = useState(false);
  useEffect(() => { setName(defaultName); setDone(false); }, [defaultName]);
  if (!open) return <button type="button" className="btn-ghost -ml-2" onClick={() => setOpen(true)}><FloppyDisk size={14} />{done ? "Saved" : "Save scenario"}</button>;
  return (
    <form className="mt-2 flex max-w-[480px] gap-2" onSubmit={async (e) => {
      e.preventDefault();
      await api.saveScenario({ name, text, shocks: shocks.map(({ symbol, kind, move }) => ({ symbol, kind, move })), events, used_events: usedEvents, horizon_weeks: horizon });
      setOpen(false); setDone(true); onSaved();
    }}>
      <input aria-label="Scenario name" dir="auto" className="field h-8 text-sm" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <button type="submit" className="btn-secondary h-8" disabled={!name.trim()}>Save</button>
      <button type="button" className="btn-ghost h-8" onClick={() => setOpen(false)}>Cancel</button>
    </form>
  );
}

// ------------------------------------------------------------------ results

function Bar({ v, max }: { v: number; max: number }) {
  const w = max > 0 ? Math.min(Math.abs(v) / max, 1) * 50 : 0;
  return (
    <div className="relative h-1.5 w-full rounded-full bg-sunken" aria-hidden>
      <div className="absolute top-0 h-full w-px bg-line-strong" style={{ left: "50%" }} />
      <div className={clsx("absolute top-0 h-full rounded-full", v < 0 ? "bg-loss" : "bg-gain")} style={v < 0 ? { right: "50%", width: `${w}%` } : { left: "50%", width: `${w}%` }} />
    </div>
  );
}

function FamilySummary({ run, ccy, view, setView, setRegime }: { run: ScenarioRun; ccy: string; view: string; setView: (v: string) => void; setRegime: (r: Regime) => void }) {
  const f = run.family;
  const rows = [...run.portfolios].sort((a, b) => a.impact - b.impact);
  const max = Math.max(...rows.map((r) => Math.abs(r.impact)), Math.abs(f.impact), 0.001);
  const h = run.history;
  const Choice = ({ r, label, impact, change }: { r: Regime; label: string; impact: number; change: number }) => {
    const on = run.regime === r;
    return (
      <button type="button" aria-pressed={on} onClick={() => setRegime(r)}
        className={clsx("press rounded-sm border px-3 py-2 text-left", on ? "border-accent bg-accent-soft" : "border-line hover:bg-hover")}>
        <span className={clsx("block text-xs", on ? "font-medium text-text" : "text-muted")}>{label}</span>
        <span className="flex items-baseline gap-2">
          <Signed value={impact} className={clsx(on ? "text-3xl font-semibold tracking-tight" : "text-xl")}>{pct(impact, 1)}</Signed>
          <Signed value={change} className={clsx(on ? "text-base" : "text-sm")}>{money(change, ccy, { signed: true })}</Signed>
        </span>
      </button>
    );
  };
  return (
    <div className="mt-12">
      <p className="text-sm text-muted">{run.portfolios.length > 1 ? "All portfolios together" : run.portfolios[0]?.name}</p>
      <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Market regime">
        <Choice r="normal" label="Normal markets" impact={f.impact_normal} change={f.change_normal} />
        <Choice r="selloff" label="In a sell-off" impact={f.impact_selloff} change={f.change_selloff} />
      </div>
      <p className="mt-2 text-sm text-muted">
        Likely between <span className="num">{pct(f.low, 1)}</span> and <span className="num">{pct(f.high, 1)}</span>
        <InfoTip text="About 2 in 3 outcomes fall in this range, given the moves above. “In a sell-off” uses how prices moved together in the worst 15% of weeks, when they tend to fall together. Click either number to use it everywhere below." />
      </p>
      {h && (
        <p className="mt-1 text-sm text-muted">
          Worst past event: {h.worst.name} <Signed value={h.worst.impact}>{pct(h.worst.impact, 1)}</Signed> (<Signed value={h.worst.change}>{money(h.worst.change, ccy, { signed: true })}</Signed>)
          {h.n > 1 && <> · Best: {h.best.name} <Signed value={h.best.impact}>{pct(h.best.impact, 1)}</Signed></>}
          <span className="block text-xs text-faint">Based on {h.n} event{h.n === 1 ? "" : "s"}, {h.from === h.to ? h.from : `${h.from}–${h.to}`}.</span>
        </p>
      )}
      {run.portfolios.length > 1 && (
        <div className="mt-6 overflow-x-auto">
          <table className="w-full text-sm sm:min-w-[520px]">
            <thead><tr className="border-b border-line text-xs text-muted">
              <th className="py-2 text-left font-medium">Portfolio</th><th className="hidden w-[28%] py-2 font-medium sm:table-cell" />
              <th className="py-2 text-right font-medium">Impact</th><th className="py-2 text-right font-medium">Amount</th>
              <th className="hidden py-2 text-right font-medium sm:table-cell">Range</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={clsx("cursor-pointer border-b border-line hover:bg-hover", view === String(r.id) && "bg-hover")} onClick={() => setView(view === String(r.id) ? "family" : String(r.id))}>
                  <td className="py-2.5"><span className="font-medium">{r.name}</span>
                    {r.losers?.[0] && <span className="ml-2 hidden text-xs text-faint sm:inline">most hit: <span className="font-mono">{r.losers[0].symbol}</span></span>}</td>
                  <td className="hidden px-3 py-2.5 sm:table-cell"><Bar v={r.impact} max={max} /></td>
                  <td className="py-2.5 text-right"><Signed value={r.impact}>{pct(r.impact, 1)}</Signed></td>
                  <td className="py-2.5 text-right"><Signed value={r.change}>{money(r.change, ccy, { signed: true })}</Signed></td>
                  <td className="num hidden py-2.5 text-right text-xs text-muted sm:table-cell">{pct(r.low, 1)} to {pct(r.high, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-faint">Click a portfolio to see its holdings and protection ideas.</p>
        </div>
      )}
    </div>
  );
}

function Market({ run }: { run: ScenarioRun }) {
  const moved = run.market.filter((m) => Math.abs(m.move) >= 0.003 || m.shocked);
  const up = moved.filter((m) => m.move > 0).sort((a, b) => b.move - a.move);
  const down = moved.filter((m) => m.move <= 0).sort((a, b) => a.move - b.move);
  const Col = ({ title, items }: { title: string; items: typeof moved }) => (
    <div>
      <h3 className="text-sm font-medium text-muted">{title}</h3>
      <ul className="mt-2">{items.slice(0, 8).map((m) => (
        <li key={m.symbol} className="flex items-baseline justify-between gap-3 border-b border-line py-1.5 text-sm">
          <span className="min-w-0 truncate">{m.label}{(m.shocked || m.pinned) && <span className="ml-1.5 text-xs text-faint">{m.pinned ? "held still" : "your move"}</span>}</span>
          <Signed value={m.move} className="shrink-0">{pct(m.move, 1)}</Signed>
        </li>))}
        {items.length === 0 && <li className="py-1.5 text-sm text-faint">Nothing much</li>}
      </ul>
    </div>
  );
  return (
    <div className="mt-12">
      <h2 className="text-base font-semibold">What else moves</h2>
      <p className="mt-1 text-sm text-muted">Expected moves of the market given yours, from how these have moved together.</p>
      <div className="mt-4 grid gap-8 sm:grid-cols-2"><Col title="Rises" items={up} /><Col title="Falls" items={down} /></div>
    </div>
  );
}

function driverText(h: ImpactHolding, run: ScenarioRun) {
  if (h.shocked) return "your move";
  const lab = (s: string) => run.market.find((m) => m.symbol === s)?.label ?? s;
  return (h.drivers ?? []).map((d) => `${lab(d.symbol)} ${pct(d.effect, 1)}`).join(", ");
}

function HoldingsImpact({ run, view, ccy }: { run: ScenarioRun; view: string; ccy: string }) {
  const [all, setAll] = useState(false);
  const rows = view === "family" ? run.family.holdings : (run.portfolio_holdings[view] ?? []).map((h) => ({ ...h, drivers: run.family.holdings.find((x) => x.symbol === h.symbol)?.drivers }));
  const name = view === "family" ? (run.portfolios.length > 1 ? "everyone" : run.portfolios[0]?.name) : run.portfolios.find((p) => String(p.id) === view)?.name;
  const shown = all ? rows : rows.slice(0, 12);
  return (
    <div className="mt-12">
      <h2 className="text-base font-semibold">Holdings · {name}</h2>
      <p className="mt-1 text-sm text-muted">Biggest hits first. "Why" shows the moves that drive each one.</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm md:min-w-[640px]">
          <thead><tr className="border-b border-line text-xs text-muted">
            <th className="py-2 text-left font-medium">Holding</th><th className="hidden py-2 text-right font-medium sm:table-cell">Weight</th>
            <th className="py-2 text-right font-medium">Move</th><th className="hidden py-2 text-right font-medium sm:table-cell">Range</th>
            <th className="py-2 text-right font-medium">Amount</th><th className="hidden py-2 pl-4 text-left font-medium md:table-cell">Why</th>
          </tr></thead>
          <tbody>{shown.map((h) => (
            <tr key={h.symbol} className="border-b border-line">
              <td className="max-w-[220px] py-2"><span className="inline-flex items-center gap-2 align-middle"><Avatar ticker={h.symbol} size={20} /><span className="font-mono font-semibold">{h.symbol}</span></span>
                <span className="ml-2 hidden truncate text-xs text-faint sm:inline">{h.name !== h.symbol ? h.name : ""}</span>
                {h.proxy && <span className="ml-1 text-xs text-faint">(via {h.proxy})</span>}</td>
              <td className="num hidden py-2 text-right text-muted sm:table-cell">{pct(h.weight, 1, false)}</td>
              <td className="py-2 text-right"><Signed value={h.move}>{pct(h.move, 1)}</Signed></td>
              <td className="num hidden py-2 text-right text-xs text-muted sm:table-cell">{h.shocked ? "–" : `${pct(h.low, 1)} to ${pct(h.high, 1)}`}</td>
              <td className="py-2 text-right"><Signed value={h.change}>{money(h.change, ccy, { signed: true })}</Signed></td>
              <td className="hidden py-2 pl-4 text-xs text-muted md:table-cell">{driverText(h, run)}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
      {rows.length > 12 && <button type="button" className="btn-ghost -ml-2 mt-2" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>}
      {rows.some((h) => h.proxy) && <p className="mt-2 text-xs text-faint">"via" = too little price history of its own, so it is measured through a sector fund or the S&P 500, with a wider range.</p>}
    </div>
  );
}

function ideaText(i: Idea, ccy: string): { title: string; body: string } {
  const size = `${pct(i.share_of_portfolio, 0, false)} of the portfolio · ${money(i.amount, ccy)}`;
  const covers = `Covers ${pct(i.covers, 0, false)} of the hit.`;
  switch (i.kind) {
    case "trim": return { title: `Trim ${i.symbol} by half`, body: `${size}, moved to cash. ${covers}` };
    case "rebalance": return { title: `Bring ${i.symbols.join(", ")} down to ${pct(i.target, 0, false)}${i.symbols.length > 1 ? " each" : ""} of the portfolio`, body: `${size}, moved to cash. ${covers}` };
    case "cash": return { title: "Raise cash: sell a quarter of every holding", body: `${size}. ${covers}` };
    case "add": return { title: `Shift into ${i.symbol}`, body: `${size}, taken evenly from your holdings. ${i.symbol} rose ${pct(i.move, 1)} in this case. ${covers}` };
    case "short": return { title: `Short ${i.symbol}${i.inverse ? ` (or buy ${i.inverse}, its inverse fund)` : ""}`, body: `${size}. ${covers} You give up as much upside if markets rise.` };
  }
}

function Protection({ run, view, ccy }: { run: ScenarioRun; view: string; ccy: string }) {
  const p = run.protection[view];
  const name = view === "family" ? "the family" : run.portfolios.find((x) => String(x.id) === view)?.name;
  if (!p) return null;
  return (
    <div className="mt-12">
      <h2 className="flex items-center gap-2 text-base font-semibold"><ShieldCheck size={18} className="text-accent" />Protection ideas · {name}</h2>
      {p.status === "not_needed" ? <p className="mt-2 text-sm text-muted">This scenario doesn't hurt {name} enough to need protection, and no past event used cost it more than 2%.</p> : (
        <>
          {p.basis && <p className="mt-1 text-sm text-muted">
            {p.basis.kind === "event" ? <>Sized for the worst past event used: {p.basis.name} (<Signed value={p.basis.impact}>{pct(p.basis.impact, 1)}</Signed>).</>
              : <>Sized for the estimate above (<Signed value={p.basis.impact}>{pct(p.basis.impact, 1)}</Signed>).</>}
          </p>}
          <ul className="mt-3 border-t border-line">{p.ideas.map((i, k) => {
            const l = ideaText(i, ccy);
            return <li key={k} className="border-b border-line py-3"><p className="text-sm font-medium">{l.title}</p><p className="num mt-0.5 text-sm text-muted">{l.body}</p></li>;
          })}</ul>
          {p.weak && <p className="mt-2 text-sm text-muted">None of these ideas, capped at half the portfolio, covers 20% of the hit.</p>}
        </>
      )}
      <p className="mt-2 text-xs text-faint">
        Each idea is capped at half the portfolio. {run.allow_shorts ? "Shorts and inverse funds are on (Settings)." : "Long-only: shorts and inverse funds are off (Settings)."} Ideas, not advice.
      </p>
    </div>
  );
}

function PastEvents({ events, ccy }: { events: EventReplay[]; ccy: string }) {
  if (!events.length) return null;
  return (
    <div className="mt-12">
      <h2 className="text-base font-semibold">Past events like this</h2>
      <p className="mt-1 text-sm text-muted">Today's holdings replayed through what really happened. Approximate windows.</p>
      <ul className="mt-4 grid gap-x-8 gap-y-6 md:grid-cols-2">
        {events.map((e) => (
          <li key={e.id} className="border-t border-line pt-3">
            <div className="flex items-baseline justify-between gap-3">
              <div className="min-w-0"><p className="font-medium">{e.name}</p><p className="text-xs text-faint">{dateLong(e.start)} to {dateLong(e.end)}</p>
                {!e.used && <p className="mt-0.5 text-xs font-medium text-muted">Not used in the estimate</p>}</div>
              <div className="shrink-0 text-right"><Signed value={e.family.impact} className="text-lg font-semibold">{pct(e.family.impact, 1)}</Signed>
                <p><Signed value={e.family.change} className="text-xs">{money(e.family.change, ccy, { signed: true })}</Signed></p></div>
            </div>
            <p className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
              {e.factors.map((f) => <span key={f.symbol}>{f.label.replace(/ \(.*\)/, "")} <Signed value={f.move}>{pct(f.move, 1)}</Signed></span>)}
            </p>
            {e.portfolios.length > 1 && <p className="mt-1.5 flex flex-wrap gap-x-3 text-xs text-muted">
              {e.portfolios.map((p) => <span key={p.id}>{p.name} <Signed value={p.impact}>{pct(p.impact, 1)}</Signed></span>)}</p>}
            {e.family.estimated_share > 0.05 && <p className="mt-1 text-xs text-faint">{pct(e.family.estimated_share, 0, false)} of the value didn't trade then and is estimated.</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Reverse({ run }: { run: ScenarioRun }) {
  const r = run.reverse;
  if (!r) return null;
  const lab = (s: string) => run.market.find((m) => m.symbol === s)?.label ?? s;
  return (
    <div className="mt-12">
      <h2 className="text-base font-semibold">How big before it costs 10%?</h2>
      {r.beyond ? <p className="mt-1 text-sm text-muted">No past event was that large.</p> : (
        <p className="mt-1 max-w-[72ch] text-sm text-muted">
          The same story <span className="num font-medium text-text">{r.scale.toFixed(1)}×</span> as strong would cost {run.portfolios.length > 1 ? "the family" : "the portfolio"} 10%:{" "}
          {r.shocks.map((s, i) => <span key={s.symbol}>{i > 0 && ", "}{lab(s.symbol)} <Signed value={s.move}>{pct(s.move, 0)}</Signed></span>)}.{" "}
          {r.years ? `About once every ${r.years < 1 ? "few months" : `${Math.round(r.years)} years`} in the data.` : ""}
        </p>
      )}
    </div>
  );
}

function ModelCheckPanel() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ModelCheck | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const started = useRef(false);
  useEffect(() => {
    if (!open || started.current) return;
    started.current = true;
    api.scenarioCheck().then(setData).catch((e) => setErr(e instanceof ApiError ? e.message : "Couldn't run the check."));
  }, [open]);
  const s = data?.summary;
  return (
    <div className="mt-12 border-t border-line pt-4">
      <button type="button" className="press flex items-center gap-2 text-base font-semibold" aria-expanded={open} onClick={() => setOpen(!open)}>
        How reliable is this?<CaretDown size={14} className={clsx("transition-transform duration-fast", open && "rotate-180")} />
      </button>
      {open && (
        <div className="fade-in mt-3">
          <p className="max-w-[72ch] text-sm text-muted">For each past event, the model is built only from the three years before it, given that event's real moves in a few factors, and asked to predict today's holdings. Then it's compared with what they really did, and with a simple rule: "everything moves like the S&P 500".</p>
          {err && <ErrorLine message={err} />}
          {!data && !err && <div className="mt-4 space-y-2"><Skel className="h-4 w-2/3" /><Skel className="h-24 w-full" /></div>}
          {data && !s && <p className="mt-3 text-sm text-muted">Not enough history in these holdings to test the model yet.</p>}
          {s && (
            <>
              <p className="mt-4 text-sm">
                Across <span className="num font-medium">{s.events}</span> events and <span className="num font-medium">{s.predictions}</span> predictions, it got the direction right{" "}
                <span className="num font-medium">{pct(s.hit_rate, 0, false)}</span> of the time. Typical miss per holding:{" "}
                <span className="num font-medium">{pct(s.mae, 1, false)}</span> (the simple rule: <span className="num">{pct(s.mae_naive, 1, false)}</span>
                {s.mae < s.mae_naive ? ", so the model adds information" : ", so for these holdings it doesn't beat the simple rule; lean on the ranges and the past events"}).
                Typical miss for the whole family: <span className="num font-medium">{pct(s.family_mae, 1, false)}</span>.
                {" "}<span className="num">{pct(s.in_range, 0, false)}</span> of outcomes landed inside the range (the aim is about 2 in 3).
              </p>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead><tr className="border-b border-line text-xs text-muted">
                    <th className="py-2 text-left font-medium">Event</th><th className="py-2 text-right font-medium">S&P 500</th>
                    <th className="py-2 text-right font-medium">Predicted</th><th className="py-2 text-right font-medium">Actual</th>
                    <th className="py-2 text-right font-medium">Direction right</th>
                  </tr></thead>
                  <tbody>{data!.events.map((e) => (
                    <tr key={e.id} className="border-b border-line">
                      <td className="py-2"><span>{e.name}</span><span className="ml-2 text-xs text-faint">{e.start.slice(0, 4)}</span></td>
                      <td className="py-2 text-right"><Signed value={e.sp500}>{pct(e.sp500, 1)}</Signed></td>
                      <td className="py-2 text-right"><Signed value={e.family_predicted}>{pct(e.family_predicted, 1)}</Signed></td>
                      <td className="py-2 text-right"><Signed value={e.family_actual}>{pct(e.family_actual, 1)}</Signed></td>
                      <td className="num py-2 text-right text-muted">{e.hit_rate == null ? "–" : pct(e.hit_rate, 0, false)}</td>
                    </tr>))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
