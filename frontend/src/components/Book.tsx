import { Plus, X } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { api, ApiError, type BookCompare, type BookEntry, type BookState } from "../lib/api";
import { amt, dateLong, pct } from "../lib/format";
import { invalidate, useData } from "../lib/hooks";
import { EmptyState, ErrorLine, InfoTip, Signed, Skel } from "./ui";

const fmt = (v: number | null | undefined, signed = true) => amt(v, signed);
const HORIZON: Record<string, string> = { "1w": "1 week", "2w": "2 weeks", "1m": "1 month", "3m": "3 months", "6m": "6 months", "12m": "12 months" };

export function Book() {
  const q = useData(api.book, "book");
  const avail = useData(api.bookScenarios, "book-scenarios");
  const [entries, setEntries] = useState<BookEntry[] | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [state, setState] = useState<BookState | null>(null);
  useEffect(() => { if (q.data) { setState(q.data); setEntries(q.data.entries); } }, [q.data]);
  if (q.error && !state) return <ErrorLine message={q.error.message} onRetry={q.reload} />;
  if (!state || !entries) return <div className="mt-8 space-y-3"><Skel className="h-24 w-full" /><Skel className="h-40 w-full" /></div>;
  const total = entries.reduce((s, e) => s + e.probability, 0);
  const dirty = JSON.stringify(entries.map((e) => [e.scenario_id, e.probability])) !== JSON.stringify(state.entries.map((e) => [e.scenario_id, e.probability]));
  const title = (id: string) => avail.data?.find((s) => s.id === id)?.title ?? entries.find((e) => e.scenario_id === id)?.title ?? id;
  const save = async () => {
    setBusy(true); setErr(null);
    try { const s = await api.saveBook(entries, note); setState(s); setEntries(s.entries); setNote(""); invalidate(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); } finally { setBusy(false); }
  };
  const free = (avail.data ?? []).filter((s) => !entries.some((e) => e.scenario_id === s.id));
  const r = state.result;
  return (
    <section className="mt-8">
      <p className="max-w-[70ch] text-sm text-muted">Your view of the world: saved scenarios, each with your chance that it happens within its horizon. Whatever is left is “nothing special”.</p>
      {(avail.data?.length ?? 0) === 0 ? (
        <EmptyState title="No saved scenarios yet." body="Save a typed scenario or import a researched one, then add it here with your probability." />
      ) : (
        <div className="mt-6 max-w-[760px]">
          <ul className="border-t border-line">
            {entries.map((e, i) => (
              <li key={e.scenario_id} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 border-b border-line py-3 sm:grid-cols-[minmax(0,1fr)_220px_auto]">
                <span className="min-w-0 truncate text-sm font-medium" dir="auto">{title(e.scenario_id)}</span>
                <span className="col-span-2 flex items-center gap-3 sm:col-span-1">
                  <input type="range" min={0} max={100} step={1} value={Math.round(e.probability * 100)} aria-label={`Chance of ${title(e.scenario_id)}`}
                    className="w-full accent-[var(--accent)]" onChange={(ev) => setEntries(entries.map((x, j) => (j === i ? { ...x, probability: Number(ev.target.value) / 100 } : x)))} />
                  <span className="num w-10 text-right text-sm">{Math.round(e.probability * 100)}%</span>
                </span>
                <button type="button" aria-label={`Remove ${title(e.scenario_id)}`} className="btn-ghost col-start-2 row-start-1 h-8 w-8 px-0 sm:col-start-3" onClick={() => setEntries(entries.filter((_, j) => j !== i))}><X size={14} /></button>
              </li>
            ))}
            <li className="flex items-center justify-between border-b border-line py-3 text-sm">
              <span className="text-muted">Nothing special</span>
              <span className={clsx("num", total > 1 ? "text-loss" : "text-muted")}>{total > 1 ? `over by ${pct(total - 1, 0, false)}` : pct(1 - total, 0, false)}</span>
            </li>
          </ul>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {free.length > 0 && (
              <select aria-label="Add a scenario" className="field h-9 w-auto max-w-[320px]" value="" onChange={(ev) => { if (ev.target.value) setEntries([...entries, { scenario_id: ev.target.value, probability: 0.1 }]); }}>
                <option value="">Add a scenario…</option>{free.map((s) => <option key={s.id} value={s.id}>{s.title} ({HORIZON[s.horizon] ?? s.horizon})</option>)}
              </select>
            )}
            <input aria-label="Note for this version" className="field h-9 w-56 text-sm" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
            <button type="button" className="btn-primary" disabled={!dirty || total > 1 || busy} onClick={save}><Plus size={14} />{busy ? "Saving" : "Save book"}</button>
            {state.saved_at && <span className="text-xs text-faint">Saved {dateLong(state.saved_at.slice(0, 10))}</span>}
          </div>
          {err && <p className="error-text">{err}</p>}
        </div>
      )}

      {r && !r.empty && (
        <div className={clsx("mt-12", dirty && "opacity-60")}>
          {dirty && <p className="mb-2 text-xs text-faint">Showing the saved book. Save to update.</p>}
          <p className="max-w-[60ch] text-2xl font-medium leading-snug">
            Across the book, most likely: <Signed value={r.answer.median_amt}>{fmt(r.answer.median_amt)}</Signed> (<Signed value={r.answer.median}>{pct(r.answer.median, 1)}</Signed>).{" "}
            <span className="text-muted">Bad case (1 in 20): <Signed value={r.answer.p5_amt}>{fmt(r.answer.p5_amt)}</Signed>.</span>
          </p>
          <p className="mt-1 text-sm text-muted">Over {HORIZON[r.horizon] ?? r.horizon}. Stock performance only, currency effects excluded{r.most_fragile && <> · most fragile: <span className="font-medium text-text">{r.most_fragile.name}</span></>}.</p>
          <Positioning state={state} />
        </div>
      )}
      <Signposts state={state} />
      <Versions onLoaded={q.reload} />
    </section>
  );
}

function Positioning({ state }: { state: BookState }) {
  const r = state.result;
  const scen = Object.entries(r.scenario_titles).filter(([id]) => r.positioning[0]?.by_scenario.some((x) => x.scenario_id === id));
  return (
    <div className="mt-8 overflow-x-auto">
      <table className="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
        <thead><tr className="text-xs text-muted">
          <th className="border-b border-line py-2 text-left font-medium">Portfolio</th>
          <th className="border-b border-line py-2 pl-3 text-right font-medium"><span className="inline-flex items-center gap-1">Fragility<InfoTip text="Average loss in the worst 10% of outcomes across the whole book." /></span></th>
          {scen.map(([id, t]) => <th key={id} className="max-w-[140px] border-b border-line py-2 pl-3 text-right font-medium"><span className="line-clamp-2">{t}</span></th>)}
        </tr></thead>
        <tbody>{r.positioning.map((p) => (
          <tr key={p.id ?? "family"} className={clsx(r.most_fragile?.id === p.id && "bg-accent-soft")}>
            <td className={clsx("border-b border-line py-2.5", p.id == null && "font-semibold")}>{p.name}</td>
            <td className="num border-b border-line py-2.5 pl-3 text-right"><Signed value={-p.fragility}>{pct(-p.fragility, 1)}</Signed> <span className="text-xs text-faint">{fmt(-p.fragility_amt)}</span></td>
            {scen.map(([id]) => {
              const x = p.by_scenario.find((y) => y.scenario_id === id);
              const worst = p.hurts_most[0]?.title === r.scenario_titles[id];
              return <td key={id} className={clsx("num border-b border-line py-2.5 pl-3 text-right", worst && "font-semibold")}>{x ? <Signed value={x.median}>{pct(x.median, 1)}</Signed> : "–"}</td>;
            })}
          </tr>
        ))}</tbody>
      </table>
      <p className="mt-2 text-xs text-faint">Middle outcome per scenario; bold = what hurts that portfolio most. Highlighted = most fragile.</p>
    </div>
  );
}

function Signposts({ state }: { state: BookState }) {
  if (!state.signposts.length) return null;
  const chip = { triggered: "bg-loss text-bg", near: "bg-accent-soft text-text", far: "bg-sunken text-muted", "no data": "bg-sunken text-faint", "not tracked": "bg-sunken text-faint" } as const;
  return (
    <div className="mt-12">
      <h3 className="text-base font-semibold">Signposts</h3>
      <p className="mt-1 text-xs text-muted">Every signpost in the book, against the latest price. Checked whenever prices refresh.</p>
      <ul className="mt-3 border-t border-line text-sm">{state.signposts.map((s, i) => (
        <li key={i} className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 border-b border-line py-2.5 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]">
          <span className="min-w-0"><span className="font-medium">{s.indicator}</span>{s.symbol && <span className="ml-1.5 font-mono text-xs text-faint">{s.symbol}</span>}
            <span className="num ml-1.5">{s.condition} {s.value}</span><span className="block truncate text-xs text-faint">{s.scenario} · {s.meaning}</span></span>
          <span className="num col-span-2 text-xs text-muted sm:col-span-1 sm:text-right">{s.current != null ? <>now {s.current.toFixed(2)}{s.distance != null && s.status !== "triggered" && <> · {pct(Math.abs(s.distance), 1, false)} away</>}</> : ""}</span>
          <span className={clsx("col-start-2 row-start-1 h-6 self-start rounded-sm px-2 text-xs leading-6 sm:col-start-3", chip[s.status])}>{s.status}</span>
        </li>
      ))}</ul>
    </div>
  );
}

function Versions({ onLoaded }: { onLoaded: () => void }) {
  const q = useData(api.bookVersions, "book-versions");
  const [pick, setPick] = useState<number[]>([]);
  const [cmp, setCmp] = useState<BookCompare | null>(null);
  const [busy, setBusy] = useState(false);
  void onLoaded;
  if (!q.data || q.data.length < 2) return null;
  return (
    <div className="mt-12">
      <h3 className="text-base font-semibold">History</h3>
      <p className="mt-1 text-xs text-muted">Every saved change. Pick two to compare.</p>
      <ul className="mt-3 border-t border-line text-sm">{q.data.map((v) => (
        <li key={v.id} className="flex flex-wrap items-center gap-x-3 border-b border-line py-2">
          <input type="checkbox" aria-label={`Compare version ${v.id}`} className="accent-[var(--accent)]" checked={pick.includes(v.id)}
            onChange={(e) => setPick(e.target.checked ? [...pick, v.id].slice(-2) : pick.filter((x) => x !== v.id))} />
          <span className="num text-muted">{dateLong(v.created_at.slice(0, 10))} {v.created_at.slice(11, 16)}</span>
          <span className="text-xs text-faint">{v.entries.map((e) => `${e.title} ${Math.round(e.probability * 100)}%`).join(" · ") || "empty"}</span>
          {v.note && <span className="text-xs">“{v.note}”</span>}
        </li>
      ))}</ul>
      <button type="button" className="btn-secondary mt-3" disabled={pick.length !== 2 || busy} onClick={async () => {
        setBusy(true); try { const [a, b] = [...pick].sort((x, y) => x - y); setCmp(await api.bookCompare(a, b)); } finally { setBusy(false); }
      }}>{busy ? "Comparing" : "Compare"}</button>
      {cmp && (
        <div className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
          {[cmp.a, cmp.b].map((v) => (
            <div key={v.id} className="rounded-md border border-line p-3">
              <p className="text-xs text-faint">{dateLong(v.created_at.slice(0, 10))}</p>
              {v.result ? <p className="mt-1">Most likely <Signed value={v.result.answer.median_amt}>{fmt(v.result.answer.median_amt)}</Signed> · bad case <Signed value={v.result.answer.p5_amt}>{fmt(v.result.answer.p5_amt)}</Signed>
                {v.result.most_fragile && <span className="block text-xs text-muted">most fragile: {v.result.most_fragile.name}</span>}</p> : <p className="mt-1 text-muted">Empty book</p>}
            </div>
          ))}
          <ul className="text-muted sm:col-span-2">{cmp.changes.map((c) => <li key={c.title}>{c.title}: {c.from == null ? "added" : pct(c.from, 0, false)} → {c.to == null ? "removed" : pct(c.to, 0, false)}</li>)}</ul>
        </div>
      )}
    </div>
  );
}
