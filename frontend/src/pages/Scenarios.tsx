import { CaretDown, FloppyDisk, Lightning, Plus, Sparkle, X } from "@phosphor-icons/react";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SymbolSearch } from "../components/SymbolSearch";
import { PersonActionsPanel } from "../components/Actions";
import { Book } from "../components/Book";
import { Earnings } from "../components/Earnings";
import { MemoPanel } from "../components/Memo";
import { Impact, type RegimeChoice } from "../components/Impact";
import { NewMoney } from "../components/NewMoney";
import { AiDebate } from "../components/AiDebate";
import { AiPanel } from "../components/AiPanel";
import { Researched } from "../components/Researched";
import { EmptyState, ErrorLine, Segmented, Signed, Skel, Blank } from "../components/ui";
import {
  api, ApiError, type ImpactEdit, type ImpactResult, type ModelCheck, type SavedScenario, type ScenarioLibrary,
  type ScenarioParse, type ScenarioRun, type Shock,
} from "../lib/api";
import { pct } from "../lib/format";
import { useData, useDebounced } from "../lib/hooks";
import { CustomSim, SwapSim } from "./WhatIf";

const EXAMPLES = ["מלחמה עם איראן", "מחיר הנפט יורד 20%", "תשואות האג״ח בארה״ב עולות", "אפל ידווחו דוחות חלשים", "Nasdaq falls 10%", "The Fed cuts rates"];
const HORIZONS = [{ value: "1", label: "1 week" }, { value: "2", label: "2 weeks" }, { value: "4.3", label: "1 month" }, { value: "13", label: "3 months" }];

export default function Scenarios() {
  const [mode, setMode] = useState<"ask" | "scenario" | "debate" | "researched" | "book" | "money" | "earnings" | "swap" | "custom">("ask");
  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <h1 className="text-xl font-semibold">What-if</h1>
      <p className="mt-1 max-w-[66ch] text-sm text-muted">
        {mode === "earnings" ? "Yesterday's reports: the research comes from the morning brief, the numbers from your own price data."
          : mode === "book" ? "Combine your scenarios into one view of the world, with your own probabilities."
          : mode === "money" ? "Rank where new money would hold up best across your saved scenarios."
          : mode === "researched" ? "Scenarios researched outside the app and imported, checked against real price history."
          : mode === "debate" ? "Several AI models debate a scenario; the app checks it against real prices and computes the impact."
          : mode === "ask" ? "Ask anything. Claude, ChatGPT and Grok each research it on their own and estimate the move of every holding; the app adds it up for each portfolio next to its own engine."
          : mode === "scenario" ? "Describe something that could happen. See what it would do to each portfolio, why, and what past events like it did."
          : "Replay your own money through a different choice. Same dates, same amounts, same engine as your real numbers."}
      </p>
      <div className="mt-6">
        <Segmented label="What-if tool" value={mode} onChange={setMode}
          options={[{ value: "ask", label: "Ask the analysts" }, { value: "scenario", label: "Scenarios" }, { value: "debate", label: "AI debate" }, { value: "researched", label: "Researched" }, { value: "book", label: "My book" }, { value: "money", label: "New money" }, { value: "earnings", label: "Yesterday's earnings" }, { value: "swap", label: "Swap a holding" }, { value: "custom", label: "Build a portfolio" }]} />
      </div>
      {mode === "ask" ? <AiPanel /> : mode === "scenario" ? <ScenarioLab /> : mode === "debate" ? <AiDebate /> : mode === "researched" ? <Researched /> : mode === "money" ? <NewMoney /> : mode === "book" ? <Book /> : mode === "earnings" ? <Earnings /> : mode === "swap" ? <SwapSim /> : <CustomSim />}
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
  const [impactRegime, setImpactRegime] = useState<RegimeChoice>("auto");
  const [edits, setEdits] = useState<ImpactEdit[]>([]);
  const [impact, setImpact] = useState<ImpactResult | null>(null);
  const [impactBusy, setImpactBusy] = useState(false);
  const [parseErr, setParseErr] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [run, setRun] = useState<ScenarioRun | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const read = useCallback(async (t: string) => {
    const q = t.trim();
    if (!q) return;
    setParsing(true); setParseErr(null);
    try {
      const p = await api.scenarioParse(q);
      setParsed(p); setShocks(p.shocks.map(withKey)); setHorizon(p.horizon_weeks || 1); setEvents(p.events); setUsedEvents(p.used_events ?? []); setEdits([]);
      if (!p.understood) { setRun(null); setImpact(null); }
    } catch (e) { setParseErr(e instanceof ApiError ? e.message : "Couldn't read that."); } finally { setParsing(false); }
  }, []);

  const load = (s: SavedScenario) => {
    setText(s.text); setParsed({ text: s.text, parser: "rules", label: s.name, themes: [], shocks: s.shocks, events: s.events, used_events: s.used_events ?? [], horizon_weeks: s.horizon_weeks, notes: [], understood: true });
    setShocks(s.shocks.map((x) => withKey({ ...x, basis: x.basis ?? "manual" }))); setHorizon(s.horizon_weeks); setEvents(s.events); setUsedEvents(s.used_events ?? []); setEdits([]);
  };

  // run whenever the shocks change
  const body = useMemo(() => JSON.stringify({ shocks: shocks.filter((s) => isFinite(s.move)).map(({ symbol, kind, move }) => ({ symbol, kind, move })), horizon_weeks: horizon, events, used_events: usedEvents, regime: "normal" }), [shocks, horizon, events, usedEvents]);
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
  // the impact engine, on the same moves
  useEffect(() => {
    const b = JSON.parse(debounced);
    if (!b.shocks.length) { setImpact(null); return; }
    const ctl = new AbortController();
    setImpactBusy(true);
    api.impactShocks({ shocks: b.shocks, horizon_weeks: b.horizon_weeks, used_events: b.used_events, text, regime: impactRegime === "auto" ? null : impactRegime, edits }, ctl.signal)
      .then((r) => { setImpact(r); setImpactBusy(false); })
      .catch((e) => { if (e.name !== "AbortError") { setRunErr(e instanceof ApiError ? e.message : "Couldn't run the scenario."); setImpactBusy(false); } });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced, impactRegime, edits]);

  const firstLoad = (running || impactBusy) && !impact;
  return (
    <section className="mt-8">
      <form className="relative" onSubmit={(e) => { e.preventDefault(); read(text); }}>
        <label htmlFor="scenario-text" className="sr-only">Describe a scenario</label>
        <Lightning size={20} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-accent" />
        <input id="scenario-text" dir="auto" autoComplete="off" className="field h-12 pl-12 pr-28 text-base" value={text} onChange={(e) => setText(e.target.value)}
          placeholder="What if… oil jumps 20%, war with Iran, Apple misses earnings" />
        <button type="submit" className="btn-primary absolute right-1.5 top-1/2 h-9 -translate-y-1/2" disabled={!text.trim() || parsing}>
          {parsing ? "Reading" : "Run scenario"}
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
              body="Name something that moves prices: oil, rates or bonds, gold, a sector, an index, a company, or an event like a war, a recession or tariffs. Or add a shock by hand below." />
          ) : null}
          <ShockEditor shocks={shocks} setShocks={setShocks} lib={lib.data} run={run} horizon={horizon} setHorizon={setHorizon} />
          {shocks.length > 0 && <SaveBar text={text} shocks={shocks} events={events} usedEvents={usedEvents} horizon={horizon} defaultName={parsed.label || text} onSaved={saved.reload} />}
        </div>
      )}
      {!parsed && shocks.length === 0 && (
        <div className="mt-10 max-w-[66ch] text-sm text-muted">
          <p>How it works: your sentence becomes a few moves in market factors (oil, rates, gold, sectors) or in one company. When you don't give a size, it uses a strong but ordinary week from history, or what past events of the same kind did. Everything else moves the way it has moved alongside those factors over the last ten years.</p>
          <p className="mt-2">You can edit every move before trusting the result.</p>
        </div>
      )}

      {runErr && <ErrorLine message={runErr} />}
      {(parsing || firstLoad) && <Steps step={parsing ? 0 : running ? 1 : 2} />}
      {firstLoad && <div className="mt-6 space-y-3" aria-hidden><Skel className="h-10 w-2/3" /><Skel className="h-4 w-1/2" /><Skel className="h-40 w-full" /></div>}
      {impact && !impact.empty && (
        <>
          <Impact r={impact} regime={impactRegime} setRegime={setImpactRegime} busy={impactBusy}
            onEdit={(e) => setEdits((xs) => [...xs.filter((x) => !(x.branch === e.branch && x.factor === e.factor)), e])} />
          <PersonActionsPanel body={{ ...JSON.parse(debounced), text, regime: impactRegime === "auto" ? null : impactRegime, edits }} />
          <MemoPanel body={{ ...JSON.parse(debounced), text, regime: impactRegime === "auto" ? null : impactRegime, edits }} />
          {run && !run.empty && <Reverse run={run} />}
          <ModelCheckPanel />
          <p className="mt-12 max-w-[72ch] text-xs text-faint">
            Estimates from how prices moved together in the past and each holding's factor exposures, not a forecast. Linear, no options, nothing outside past experience.
            Method: docs/SCENARIOS.md.
          </p>
        </>
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
  // the select always shows the horizon actually used (past events can give one that isn't a preset)
  const exact = HORIZONS.find((h) => Math.abs(Number(h.value) - horizon) < 1e-9);
  const horizonOptions = exact ? HORIZONS : [...HORIZONS, { value: String(horizon), label: `${Math.round(horizon * 10) / 10} weeks` }].sort((a, b) => Number(a.value) - Number(b.value));
  const horizonValue = exact ? exact.value : String(horizon);
  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-base font-semibold">The moves</h2>
        <label className="flex items-center gap-2 text-sm text-muted">Over
          <select className="field h-8 w-auto py-0 text-sm" value={horizonValue} onChange={(e) => setHorizon(Number(e.target.value))}>
            {horizonOptions.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
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
                      <td className="num py-2 text-right text-muted">{e.hit_rate == null ? <Blank why="No outcomes scored yet." /> : pct(e.hit_rate, 0, false)}</td>
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

/** What is happening while a scenario is read and run, shown only when it takes longer than a blink. */
function Steps({ step }: { step: number }) {
  const [show, setShow] = useState(false);
  useEffect(() => { const t = setTimeout(() => setShow(true), 300); return () => clearTimeout(t); }, []);
  if (!show) return null;
  const names = ["Reading the sentence", "Finding past weeks like it", "Simulating 10,000 outcomes"];
  return (
    <ol className="mt-8 space-y-1.5 text-sm" aria-live="polite" aria-label="Progress">
      {names.map((n, i) => (
        <li key={n} className={clsx("flex items-center gap-2", i < step ? "text-muted" : i === step ? "font-medium text-text" : "text-faint")}>
          <span aria-hidden className={clsx("h-1.5 w-1.5 rounded-full", i < step ? "bg-muted" : i === step ? "animate-pulse bg-text" : "border border-faint")} />
          {n}{i < step && <span className="sr-only"> (done)</span>}
        </li>
      ))}
      <li className="pt-1 text-xs text-faint">The first run loads ten years of market history. Later runs take a moment.</li>
    </ol>
  );
}
