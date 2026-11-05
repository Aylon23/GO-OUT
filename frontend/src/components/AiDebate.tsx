import clsx from "clsx";
import { useEffect, useState } from "react";
import { api, ApiError, type DebateMover, type DebateRow } from "../lib/api";
import { dateLong, money, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { AiQuota } from "./AiQuota";
import { Avatar } from "./Avatar";
import { FACTOR_LABEL, Report, SpecImpact } from "./Researched";
import { ErrorLine, Signed, Blank } from "./ui";

const HORIZON_LABEL: Record<string, string> = { "1w": "1 week", "1m": "1 month", "3m": "3 months", "6m": "6 months" };
const PHASE: Record<string, string> = { round1: "Round 1: each model writes its own scenario", round2: "Round 2: they challenge each other", judge: "The judge merges them", done: "Done", failed: "Stopped" };
const EXAMPLES = ["תהיה מלחמה עם איראן", "oil falls $10 per barrel", "The Fed cuts rates by 0.5% next month"];

/** Scenarios → AI debate: several models write and challenge a scenario; the engine computes the impact. */
export function AiDebate() {
  const [text, setText] = useState("");
  const [needs, setNeeds] = useState<{ horizon?: string[]; cause?: string[] } | null>(null);
  const [horizon, setHorizon] = useState<string | undefined>();
  const [cause, setCause] = useState<string | undefined>();
  const [read, setRead] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const list = useData(api.debates, "debates");
  const [openId, setOpenId] = useState<number | null>(null);
  const shown = openId ?? list.data?.[0]?.id ?? null;

  const start = async (h = horizon, c = cause) => {
    if (!text.trim()) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.debateStart({ text, horizon: h, cause: c });
      if ("needs" in r) { setNeeds(r.needs); setRead(r.read.dollar_moves.map((d) => d.text ?? "").filter(Boolean)); return; }
      setNeeds(null); setHorizon(undefined); setCause(undefined); setRead([]);
      setOpenId(r.id); list.reload();
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't start the debate."); } finally { setBusy(false); }
  };
  const ready = needs && (!needs.horizon || horizon) && (!needs.cause || cause);

  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-[70ch] text-sm text-muted">Four AI models each write the scenario on their own, then challenge each other; a judge merges them. The app checks the result against real price history and computes every number.</p>
        <AiQuota />
      </div>
      <form className="mt-4 flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); start(); }}>
        <input aria-label="Scenario" dir="auto" className="field h-10 flex-1" placeholder="Describe what could happen, in English or Hebrew" value={text}
          onChange={(e) => { setText(e.target.value); setNeeds(null); }} />
        <button type="submit" className="btn-primary h-10" disabled={busy || !text.trim()}>{busy ? "Reading…" : "Run debate"}</button>
      </form>
      {!text && <div className="mt-2 flex flex-wrap gap-2">{EXAMPLES.map((x) => <button key={x} type="button" dir="auto" className="chip" onClick={() => setText(x)}>{x}</button>)}</div>}
      {needs && (
        <div className="mt-4 space-y-3 rounded-sm border border-line p-3 text-sm">
          {read.length > 0 && <p className="text-muted">Read: {read.join("; ")}</p>}
          {needs.horizon && <Chips label="Over what time?" options={needs.horizon} value={horizon} onPick={setHorizon} fmt={(h) => HORIZON_LABEL[h] ?? h} />}
          {needs.cause && <Chips label="What kind of shock is it?" options={needs.cause} value={cause} onPick={setCause} fmt={(c) => c[0].toUpperCase() + c.slice(1)} />}
          <button type="button" className="btn-primary" disabled={!ready || busy} onClick={() => start(horizon, cause)}>Run debate</button>
        </div>
      )}
      {err && <ErrorLine message={err} />}
      {list.data && list.data.length > 1 && (
        <div className="mt-6 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Earlier:</span>
          <select aria-label="Earlier debates" className="field h-8 w-auto max-w-full py-0 text-sm" value={shown ?? ""} onChange={(e) => setOpenId(Number(e.target.value))}>
            {list.data.map((d) => <option key={d.id} value={d.id}>{dateLong(d.created_at.slice(0, 10))}: {d.text.slice(0, 60)}</option>)}
          </select>
        </div>
      )}
      {shown != null && <DebateView key={shown} id={shown} onChange={list.reload} />}
      <Leaderboard />
    </section>
  );
}

function Chips({ label, options, value, onPick, fmt }: { label: string; options: string[]; value?: string; onPick: (v: string) => void; fmt: (v: string) => string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap items-center gap-2">
      <span className="mr-1">{label}</span>
      {options.map((o) => <button key={o} type="button" role="radio" aria-checked={value === o} className={clsx("chip", value === o && "chip-on")} onClick={() => onPick(o)}>{fmt(o)}</button>)}
    </div>
  );
}

function DebateView({ id, onChange }: { id: number; onChange: () => void }) {
  const [d, setD] = useState<DebateRow | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let t: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await api.debate(id);
        if (!live) return;
        setD(r);
        if (r.phase !== "done" && r.phase !== "failed") t = setTimeout(tick, 3000);
        else onChange();
      } catch (e) { if (live) setErr(e instanceof ApiError ? e.message : "Couldn't load it."); }
    };
    tick();
    return () => { live = false; clearTimeout(t); };
  }, [id, onChange]);
  if (err) return <ErrorLine message={err} />;
  if (!d) return null;
  const r = d.result;
  const ccy = r?.currency ?? "USD";
  if (d.phase !== "done") {
    return (
      <div className="mt-8 text-sm" aria-live="polite">
        <p className="font-medium" dir="auto">{d.text}</p>
        <p className={clsx("mt-1", d.phase === "failed" ? "text-loss" : "text-muted")}>{d.phase === "failed" ? d.note : PHASE[d.phase]} · {d.progress.done} of {d.progress.total} answers · {d.requests} requests</p>
        {d.waiting && <p className="mt-1 text-muted">{d.waiting}</p>}
      </div>
    );
  }
  const act = async (fn: () => Promise<string>) => { setMsg(null); try { setMsg(await fn()); } catch (e) { setMsg(e instanceof ApiError ? e.message : "That didn't work."); } };
  return (
    <article className="mt-10" aria-label="Debate result">
      <p className="text-xs text-muted" dir="auto">{d.text} · {HORIZON_LABEL[d.horizon] ?? d.horizon} · {d.cause}</p>
      {r?.answer && <h2 className="mt-2 max-w-[60ch] text-xl font-semibold leading-snug">{r.answer}</h2>}
      <p className="mt-2 text-xs text-faint">AI opinion based on the data shown. Not advice. {d.requests} requests used.</p>

      {r?.branches && (
        <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{r.branches.map((b) => (
          <li key={b.name} className="rounded-sm border border-line p-3">
            <div className="flex items-baseline justify-between gap-2"><span className="font-medium">{b.name}</span><span className="num text-sm text-muted">{pct(b.probability, 0, false)}</span></div>
            <p className="num mt-1 text-sm"><Signed value={b.family.median}>{pct(b.family.median, 1)}</Signed> <span className="text-muted">family, range {pct(b.family.p5, 1)} to {pct(b.family.p95, 1)}</span></p>
          </li>
        ))}</ul>
      )}

      <div className="mt-8 grid gap-8 md:grid-cols-2">
        <Movers title="Most hurt" rows={r?.hurt ?? []} ccy={ccy} />
        <Movers title="Most helped" rows={r?.helped ?? []} ccy={ccy} />
      </div>
      {r?.most_exposed && <p className="mt-4 text-sm">{r.most_exposed.median < 0 ? "Most exposed" : "Gains the least"}: <span className="font-medium" dir="auto">{r.most_exposed.name}</span> <Signed value={r.most_exposed.median}>{pct(r.most_exposed.median, 1)}</Signed> <span className="text-muted">({money(r.most_exposed.median_amt, ccy, { decimals: 0, signed: true })})</span></p>}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <button type="button" className="btn-secondary" onClick={() => act(async () => `Added to My book at ${pct((await api.debateBook(d.id)).probability, 0, false)}. Set your own probability there.`)}>Save to scenario book</button>
        <button type="button" className="btn-secondary" onClick={() => act(async () => { const n = (await api.debateQueue(d.id)).added; return n ? `${n} suggestions added to the action queue.` : "Nothing loses money in this scenario."; })}>Send top items to action queue</button>
        {msg && <span className="text-sm text-muted" role="status">{msg}</span>}
      </div>

      {r && <Level2 d={d} />}
      {r && <Level3 d={d} />}
    </article>
  );
}

function Movers({ title, rows, ccy }: { title: string; rows: DebateMover[]; ccy: string }) {
  return (
    <div>
      <h3 className="text-sm font-semibold">{title}</h3>
      {rows.length === 0 ? <p className="mt-2 text-sm text-faint">None.</p> : (
        <ul className="mt-2 divide-y divide-line">{rows.map((h) => (
          <li key={h.symbol} className="flex items-center gap-3 py-2 text-sm">
            <Avatar ticker={h.symbol} size={24} />
            <span className="min-w-0 flex-1"><span className="font-semibold">{h.symbol}</span> <span className="text-muted">{h.driver ? `driven by ${(FACTOR_LABEL[h.driver] ?? h.driver).toLowerCase()}` : ""}</span></span>
            <span className="num text-right"><Signed value={h.move}>{pct(h.move, 1)}</Signed><span className="block text-xs text-muted">{money(h.change_amt, ccy, { decimals: 0, signed: true })}</span></span>
          </li>
        ))}</ul>
      )}
    </div>
  );
}

function Level2({ d }: { d: DebateRow }) {
  const r = d.result!;
  const [open, setOpen] = useState(false);
  const facs = Array.from(new Set((r.headline ?? []).flatMap((h) => Object.keys(h.moves)))).sort();
  return (
    <details className="mt-12 border-t border-line pt-4" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-sm font-semibold">Where the models agreed and disagreed</summary>
      <div className="mt-4 space-y-6 text-sm">
        {r.agreements.length > 0 && <div><p className="font-medium">They agreed</p><ul className="mt-1 list-disc space-y-1 pl-5">{r.agreements.map((a) => <li key={a}>{a}</li>)}</ul></div>}
        <div>
          <p className="font-medium">Disputes</p>
          {r.disputes.length === 0 ? <p className="mt-1 text-muted">None listed.</p> : (
            <ul className="mt-1 space-y-2">{r.disputes.map((x, i) => (
              <li key={i}><span className="font-medium">{x.topic}</span>{x.added_by === "app" && <span className="text-xs text-faint"> (stated by the app)</span>}: {x.views}{x.why && <span className="text-muted"> {x.why}</span>}</li>
            ))}</ul>
          )}
          {r.minority_views.length > 0 && <p className="mt-2 text-muted">Minority views: {r.minority_views.join("; ")}</p>}
        </div>
        {r.devils_case && <div><p className="font-medium">Devil's advocate (Model {d.devil})</p><p className="mt-1 max-w-[70ch]">{r.devils_case}</p>{r.devils_case_assessment && <p className="mt-1 max-w-[70ch] text-muted">Judge: {r.devils_case_assessment}</p>}</div>}
        {r.widened.length > 0 && <p className="text-muted">Ranges widened where models disagreed: {r.widened.map((w) => `${FACTOR_LABEL[w.factor] ?? w.factor} in ${w.branch} (spread ${w.spread.toFixed(1)} points)`).join("; ")}.</p>}
        {r.headline && (
          <div className="overflow-x-auto">
            <p className="font-medium">Each model's numbers</p>
            <table className="mt-2 w-full min-w-[560px] text-xs">
              <thead><tr className="text-muted">{["Model", "Round", "Family", "Bad case", ...facs.map((f) => FACTOR_LABEL[f] ?? f)].map((h, i) => <th key={h + i} className={clsx("border-b border-line py-1.5 font-medium", i > 1 ? "pl-3 text-right" : "text-left")}>{h}</th>)}</tr></thead>
              <tbody>{r.headline.map((h) => (
                <tr key={h.model + h.stage}>
                  <td className="border-b border-line py-1.5">{h.model}</td><td className="border-b border-line py-1.5 text-muted">{h.stage}</td>
                  <td className="num border-b border-line py-1.5 pl-3 text-right"><Signed value={h.median}>{pct(h.median, 1)}</Signed></td>
                  <td className="num border-b border-line py-1.5 pl-3 text-right">{pct(h.p5, 1)}</td>
                  {facs.map((f) => <td key={f} className="num border-b border-line py-1.5 pl-3 text-right">{h.moves[f] != null ? `${h.moves[f] > 0 ? "+" : ""}${h.moves[f].toFixed(1)}%` : <Blank why="No exposure to this factor estimated for this holding." />}</td>)}
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <div><p className="font-medium">Transmission chain, analogs and the full impact</p>{open && <SpecImpact id={d.spec_id} />}</div>
      </div>
    </details>
  );
}

function Level3({ d }: { d: DebateRow }) {
  const r = d.result!;
  return (
    <details className="mt-6 border-t border-line pt-4">
      <summary className="cursor-pointer text-sm font-semibold">Transcript and validation</summary>
      <div className="mt-4 space-y-5 text-xs">
        <p className="text-muted">{d.requests} requests. Model {d.devil} was the devil's advocate. {r.notes.join(" ")}</p>
        {(d.transcript ?? []).map((t, i) => (
          <details key={i} className="rounded-sm border border-line p-2">
            <summary className="cursor-pointer">{t.round.replace("round", "Round ")} · {t.label === "judge" ? "Judge" : `Model ${t.label}`} · {t.model_used || t.models[0]} · {t.status}{t.requests ? ` · ${t.requests} requests` : ""}</summary>
            <p className="mt-1 text-faint">Asked: {t.models.join(", ")}</p>
            {t.error && <p className="mt-1 text-loss">{t.error}</p>}
            {t.answer != null && <pre className="mt-2 max-h-[320px] overflow-auto whitespace-pre-wrap rounded-sm bg-sunken p-2 text-[11px]">{JSON.stringify(t.answer, null, 2)}</pre>}
          </details>
        ))}
        <div><p className="mb-1 text-sm font-medium">Validation</p><Report report={r.validation} /></div>
      </div>
    </details>
  );
}

function Leaderboard() {
  const q = useData(api.debateBoard, "debate-board");
  const rows = q.data ?? [];
  return (
    <section className="mt-16" aria-label="Scenario leaderboard">
      <h3 className="text-sm font-semibold">Scenario leaderboard</h3>
      {rows.length === 0 ? <p className="mt-1 text-xs text-faint">Each model's round-1 scenario and the judge's are scored when the horizon ends: direction, size of the error, and whether the real move landed inside the range.</p> : (
        <table className="mt-2 w-full max-w-[640px] text-xs">
          <thead><tr className="text-muted">{["Model", "Scored", "Direction right", "Average error", "Inside the range"].map((h, i) => <th key={h} className={clsx("border-b border-line py-1.5 font-medium", i ? "pl-3 text-right" : "text-left")}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((x) => (
            <tr key={x.model}><td className="border-b border-line py-1.5">{x.model}</td><td className="num border-b border-line py-1.5 pl-3 text-right">{x.scenarios}</td>
              <td className="num border-b border-line py-1.5 pl-3 text-right">{x.direction_hit_rate != null ? pct(x.direction_hit_rate, 0, false) : <Blank why="No scored debates yet: scores come in after the horizon ends." />}</td>
              <td className="num border-b border-line py-1.5 pl-3 text-right">{x.avg_error != null ? `${x.avg_error.toFixed(1)} pts` : <Blank why="No scored debates yet: scores come in after the horizon ends." />}</td>
              <td className="num border-b border-line py-1.5 pl-3 text-right">{x.in_range_rate != null ? pct(x.in_range_rate, 0, false) : <Blank why="No scored debates yet: scores come in after the horizon ends." />}</td></tr>
          ))}</tbody>
        </table>
      )}
    </section>
  );
}
