import { CaretDown } from "@phosphor-icons/react";
import clsx from "clsx";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ChatMsg, type PanelFocus, type PanelRun, type PanelSetup, type PanelStock, type PanelTable } from "../lib/api";
import { dateLong, money, pct } from "../lib/format";
import { useData, useDebounced, useMediaQuery } from "../lib/hooks";
import { Blank, ErrorLine, InfoTip, Segmented, Signed } from "./ui";

const HORIZON: Record<string, string> = { "1d": "1 day", "1w": "1 week", "2w": "2 weeks", "1m": "1 month", "3m": "3 months", "6m": "6 months", "12m": "1 year" };
const SHOWN_HORIZONS = ["1d", "1w", "1m", "3m", "6m"];
const EXAMPLES = ["אתמול היה הדוח של CRWV. סכם אותו ולאן המניה הולכת", "פורצת מלחמה עם איראן", "הריבית בארה\"ב יורדת ברבע אחוז, בניגוד לציפיות", "Oil jumps 20%"];
const STATE: Record<string, string> = { waiting: "Waiting", working: "Researching", done: "Done", failed: "No answer" };
const p1 = (v: number | null | undefined) => (v == null ? "" : pct(v / 100, 1));

/** What-if → Ask the analysts: three models research the question on their own and give a move for every
 *  holding; the app adds them up per portfolio next to its own engine. */
export function AiPanel() {
  const setup = useData(api.panelSetup, "panel-setup");
  const list = useData(api.panelRuns, "panel-runs");
  const [text, setText] = useState("");
  const [horizon, setHorizon] = useState("1m");
  const [web, setWeb] = useState(false);
  const [webByHand, setWebByHand] = useState(false);
  const typed = useDebounced(text, 400);
  // web search costs most of a question: on by itself for news, a report or one of the holdings
  useEffect(() => {
    if (webByHand) return;
    let live = true;
    if (!typed.trim()) { setWeb(false); return; }
    api.panelWeb(typed).then((r) => { if (live) setWeb(r.web); }).catch(() => {});
    return () => { live = false; };
  }, [typed, webByHand]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const shown = openId ?? list.data?.[0]?.id ?? null;
  const s = setup.data;
  const reload = useCallback(() => { list.reload(); setup.reload(); }, [list, setup]);

  const start = async () => {
    if (!text.trim()) return;
    setBusy(true); setErr(null);
    try { const r = await api.panelStart({ text, horizon, web }); setOpenId(r.id); list.reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't start."); } finally { setBusy(false); }
  };

  return (
    <section className="mt-8">
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); start(); }}>
        <label htmlFor="panel-q" className="sr-only">Your question</label>
        <input id="panel-q" dir="auto" autoComplete="off" className="field h-12 flex-1 text-base" value={text} onChange={(e) => setText(e.target.value)}
          placeholder="Ask anything: a scenario, a news event, an earnings report" />
        <button type="submit" className="btn-primary h-12" disabled={busy || !text.trim() || !s?.key_set}>{busy ? "Starting" : "Ask the analysts"}</button>
      </form>
      {!text && <div className="mt-2 flex flex-wrap gap-2">{EXAMPLES.map((x) => <button key={x} type="button" dir="auto" className="chip h-auto min-h-7 py-1 text-left" onClick={() => setText(x)}>{x}</button>)}</div>}
      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3 text-sm">
        <div role="radiogroup" aria-label="Over what time" className="flex flex-wrap items-center gap-2">
          <span className="text-muted">Over</span>
          {SHOWN_HORIZONS.map((h) => <button key={h} type="button" role="radio" aria-checked={horizon === h} className={clsx("chip", horizon === h && "chip-on")} onClick={() => setHorizon(h)}>{HORIZON[h]}</button>)}
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-muted">
          <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={web} onChange={(e) => { setWeb(e.target.checked); setWebByHand(true); }} />
          Search the web for current facts{!webByHand && text.trim() && <span className="text-faint">{web ? " (on: the question is about news or a holding)" : " (off: not needed for a what-if)"}</span>}
        </label>
      </div>
      {s && <SetupLine s={s} web={web} onSaved={setup.reload} />}
      {err && <ErrorLine message={err} />}
      {list.data && list.data.length > 1 && (
        <div className="mt-6 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Earlier:</span>
          <select aria-label="Earlier questions" className="field h-8 w-auto max-w-full py-0 text-sm" value={shown ?? ""} onChange={(e) => setOpenId(Number(e.target.value))}>
            {list.data.map((d) => <option key={d.id} value={d.id}>{dateLong(d.created_at.slice(0, 10))}: {d.text.slice(0, 60)}</option>)}
          </select>
        </div>
      )}
      {shown != null ? <RunView key={shown} id={shown} onDone={reload} onAskPanel={(q) => { setText(q); window.scrollTo({ top: 0, behavior: "smooth" }); }} />
        : s?.key_set && list.data && <Chat runId={0} onAskPanel={setText} />}
      <Board />
    </section>
  );
}

function SetupLine({ s, web, onSaved }: { s: PanelSetup; web: boolean; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [budget, setBudget] = useState(String(s.budget.limit));
  const [models, setModels] = useState<Record<string, string>>(() => Object.fromEntries(s.seats.map((x) => [x.key, x.chosen ? x.model : ""])));
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setErr(null);
    try { await api.panelSettings({ budget_usd: Number(budget), models }); onSaved(); setOpen(false); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); }
  };
  const missing = s.seats.filter((x) => !x.model);
  return (
    <div className="mt-4 text-xs text-muted">
      {!s.key_set ? <p className="text-loss">No OpenRouter key yet. Add OPENROUTER_API_KEY to the .env file next to the app and restart it.</p> : (
        <p>
          {s.seats.filter((x) => x.model).map((x) => x.label).join(", ") || "No models found"} research on their own.
          {" "}About {money(web ? s.estimate_usd.web : s.estimate_usd.no_web, "USD", { decimals: 2 })} a question. Spent {money(s.budget.spent, "USD", { decimals: 2 })} of {money(s.budget.limit, "USD", { decimals: 2 })} this month.
          {" "}<button type="button" className="link" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Models and budget</button>
        </p>
      )}
      {missing.length > 0 && s.key_set && <p className="mt-1 text-loss">Not found on OpenRouter: {missing.map((x) => x.label).join(", ")}. {missing[0].error || "Set the model id below."}</p>}
      {open && (
        <div className="mt-3 max-w-xl space-y-3 border-y border-line py-4 text-sm text-text">
          <label className="flex items-center justify-between gap-3">
            <span>Monthly budget (USD)<span className="block text-xs text-muted">Paid questions stop when it is reached, until the 1st.</span></span>
            <input type="number" min={0} max={500} step={1} className="field num h-9 w-24 text-right" value={budget} onChange={(e) => setBudget(e.target.value)} />
          </label>
          {s.seats.map((x) => (
            <label key={x.key} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
              <span>{x.label}<span className="block text-xs text-muted">{x.price_out != null ? `$${x.price_in?.toFixed(2)} in, $${x.price_out.toFixed(2)} out per million tokens` : "Price unknown"}</span></span>
              <input className="field h-9 sm:w-72" placeholder={x.auto ? `Newest: ${x.auto}` : "model id, e.g. vendor/model"} value={models[x.key] ?? ""} onChange={(e) => setModels((m) => ({ ...m, [x.key]: e.target.value }))} />
            </label>
          ))}
          <p className="text-xs text-muted">Leave a model empty to use the vendor's newest main model at a normal price. Only tickers and combined weights are sent: no names, no amounts, no split by portfolio.</p>
          {err && <ErrorLine message={err} />}
          <div className="flex gap-2"><button type="button" className="btn-primary" onClick={save}>Save</button><button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button></div>
        </div>
      )}
    </div>
  );
}

function RunView({ id, onDone, onAskPanel }: { id: number; onDone: () => void; onAskPanel: (q: string) => void }) {
  const [d, setD] = useState<PanelRun | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let t: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await api.panelRun(id);
        if (!live) return;
        setD(r);
        if (r.status === "running") t = setTimeout(tick, 1500); else onDone();
      } catch (e) { if (live) setErr(e instanceof ApiError ? e.message : "Couldn't load it."); }
    };
    tick();
    return () => { live = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  if (err) return <ErrorLine message={err} />;
  if (!d) return null;
  if (d.status === "running") return <Progress d={d} />;
  if (d.status === "failed") {
    return <div className="mt-8 text-sm"><p className="font-medium" dir="auto">{d.text}</p><p className="mt-1 text-loss">{d.note || "It stopped."}</p></div>;
  }
  return <Result d={d} onAskPanel={onAskPanel} />;
}

function Progress({ d }: { d: PanelRun }) {
  const v = Math.max(3, Math.min(100, d.progress.pct ?? 3));
  return (
    <div className="mt-8" aria-live="polite">
      <p className="text-sm font-medium" dir="auto">{d.text}</p>
      <div className="mt-3 flex items-baseline justify-between text-sm">
        <span className="text-muted">{d.progress.step ?? "Starting"}</span>
        <span className="num font-semibold">{v}%</span>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} aria-label="Progress">
        <div className="h-full rounded-full bg-accent transition-[width] duration-slow ease-out" style={{ width: `${v}%` }} />
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {d.seats.map((s) => {
          const st = d.progress.models?.[s.key] ?? "waiting";
          return (
            <li key={s.key} className="flex items-center gap-2">
              <span aria-hidden className={clsx("h-1.5 w-1.5 rounded-full", st === "done" ? "bg-text" : st === "working" ? "animate-pulse bg-accent" : st === "failed" ? "bg-loss" : "border border-faint")} />
              <span className="font-medium">{s.label}</span><span className="text-muted">{STATE[st]}</span>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-faint">Each model researches alone{d.web ? " and searches the web" : ""}. This usually takes one to three minutes; you can leave the page.</p>
    </div>
  );
}

function Result({ d, onAskPanel }: { d: PanelRun; onAskPanel: (q: string) => void }) {
  const tables = d.result?.tables ?? [];
  const [view, setView] = useState("family");
  const ccy = d.result?.currency ?? "USD";
  const seats = d.seats.filter((s) => d.analysts?.[s.key]);
  const t = tables.find((x) => (x.id == null ? "family" : String(x.id)) === view) ?? tables[0];
  const m = (v: number | null | undefined) => money(v ?? null, ccy, { decimals: 0, signed: true });
  if (!t) return null;
  return (
    <div className="mt-10">
      <p className="text-xs text-muted"><span dir="auto">{d.text}</span>, over {HORIZON[d.horizon] ?? d.horizon}, {dateLong(d.created_at.slice(0, 10))}. Cost {money(d.cost, "USD", { decimals: 2 })}.</p>
      <div className="mt-3 max-w-[70ch] space-y-1.5 text-base leading-7" dir="rtl" lang="he">
        {(d.result?.summary_he ?? []).map((line, i) => <p key={i} className={clsx(i === 0 && "text-lg font-medium", i === (d.result?.summary_he?.length ?? 0) - 1 && "text-sm text-muted")}>{line}</p>)}
      </div>
      {Object.keys(d.result?.errors ?? {}).length > 0 && (
        <p className="mt-3 text-sm text-muted">{Object.entries(d.result!.errors!).map(([k, e]) => `${d.seats.find((s) => s.key === k)?.label ?? k} did not answer (${e})`).join(". ")}.</p>
      )}

      {(d.result?.focus ?? []).map((f) => <Spotlight key={f.symbol} f={f} seats={seats} m={m} />)}

      {tables.length > 1 && (
        <div className="mt-8 overflow-x-auto"><Segmented label="Portfolio" size="sm" value={view} onChange={setView}
          options={tables.map((x) => ({ value: x.id == null ? "family" : String(x.id), label: x.name }))} /></div>
      )}
      <Totals t={t} seats={seats} m={m} engineWhy={d.result?.engine_why ?? ""} />
      <Stocks t={t} seats={seats} m={m} engineWhy={d.result?.engine_why ?? ""} />
      <Chat runId={d.id} onAskPanel={onAskPanel} />
      <Analysts d={d} />
    </div>
  );
}

/** The stock the question is about: each model's summary of the news and its call for one and three months. */
function Spotlight({ f, seats, m }: { f: PanelFocus; seats: PanelRun["seats"]; m: (v: number | null | undefined) => string }) {
  const got = seats.filter((s) => f.models[s.key]);
  const [who, setWho] = useState(got[0]?.key ?? "");
  const a = f.models[who];
  const cell = (label: string, v: number, amt: number | null, lo: number, hi: number) => (
    <div className="min-w-[150px] flex-1 border-t border-text py-3">
      <div className="text-xs text-muted">{label}</div>
      <Signed value={v} className="num block text-2xl font-semibold">{p1(v)}</Signed>
      <div className="num text-sm"><Signed value={amt}>{m(amt)}</Signed>{got.length > 1 && <span className="text-muted">, models from {p1(lo)} to {p1(hi)}</span>}</div>
    </div>
  );
  return (
    <section className="mt-10" aria-labelledby={`spot-${f.symbol}`}>
      <h2 id={`spot-${f.symbol}`} className="text-lg font-semibold"><span className="font-mono">{f.symbol}</span> <span className="font-normal text-muted">{f.name !== f.symbol && f.name}</span></h2>
      <p className="mt-1 text-sm text-muted">Where the models see the stock from here. It is {pct(f.weight, 1, false)} of all the portfolios together.</p>
      <div className="mt-4 flex flex-wrap gap-x-6">
        {cell("Next month, models' average", f.avg_1m, f.amt_1m, f.low_1m, f.high_1m)}
        {cell("Next 3 months, models' average", f.avg_3m, f.amt_3m, f.low_3m, f.high_3m)}
      </div>
      <table className="mt-2 w-full max-w-3xl text-sm">
        <thead><tr className="text-xs text-muted"><th className="border-b border-line py-2 text-left font-medium">Model</th><th className="border-b border-line py-2 pl-3 text-right font-medium">1 month</th>
          <th className="border-b border-line py-2 pl-3 text-right font-medium">3 months</th><th className="border-b border-line py-2 pl-4 text-right font-medium">Its call</th></tr></thead>
        <tbody>{seats.map((s) => {
          const c = f.models[s.key];
          return (
            <tr key={s.key}><td className="border-b border-line py-2 align-top font-medium">{s.label}</td>
              <td className="num border-b border-line py-2 pl-3 text-right align-top">{c ? <Signed value={c.m1}>{p1(c.m1)}</Signed> : <Blank why="This model gave no call on this stock." />}</td>
              <td className="num border-b border-line py-2 pl-3 text-right align-top">{c ? <Signed value={c.m3}>{p1(c.m3)}</Signed> : <Blank why="This model gave no call on this stock." />}</td>
              <td className="border-b border-line py-2 pl-4 text-muted" dir="auto">{c?.view}</td></tr>
          );
        })}</tbody>
      </table>
      {a && (
        <div className="mt-5 max-w-[72ch]">
          <div className="flex flex-wrap items-center gap-3"><h3 className="text-sm font-semibold">What happened, in short</h3>
            {got.length > 1 && <Segmented label="Whose summary" size="sm" value={who} onChange={setWho} options={got.map((s) => ({ value: s.key, label: s.label }))} />}</div>
          <p className="mt-2 whitespace-pre-line text-base leading-7" dir="auto">{a.summary}</p>
        </div>
      )}
      {f.held_in.length > 0 && (
        <div className="mt-5 max-w-3xl text-sm">
          <h3 className="font-semibold">What the average call means for each portfolio that holds it</h3>
          <ul className="mt-2">
            {f.held_in.map((h) => (
              <li key={h.id} className="grid grid-cols-[1fr_auto] gap-x-4 border-t border-line py-1.5 sm:grid-cols-[1fr_150px_130px_130px]">
                <span className="font-medium">{h.name}</span><span className="num text-right text-muted">{pct(h.weight, 1, false)} of the portfolio</span>
                <span className="num text-right text-muted sm:text-text">1 month <Signed value={h.amt_1m}>{m(h.amt_1m)}</Signed></span>
                <span className="num text-right text-muted sm:text-text">3 months <Signed value={h.amt_3m}>{m(h.amt_3m)}</Signed></span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Totals({ t, seats, m, engineWhy }: { t: PanelTable; seats: PanelRun["seats"]; m: (v: number | null | undefined) => string; engineWhy: string }) {
  const cell = (label: string, v: { pct: number; amt: number } | null, strong = false, why = "") => (
    <div key={label} className={clsx("min-w-[120px] flex-1 border-t py-3", strong ? "border-text" : "border-line")}>
      <div className="text-xs text-muted">{label}</div>
      {v ? <><Signed value={v.pct} className={clsx("num block font-semibold", strong ? "text-2xl" : "text-xl")}>{p1(v.pct)}</Signed><Signed value={v.amt} className="num text-sm">{m(v.amt)}</Signed></>
        : <div className="text-xl"><Blank why={why || "No figure."} /></div>}
    </div>
  );
  return (
    <div className="mt-5 flex flex-wrap gap-x-6">
      {cell("Models' average", t.avg, true)}
      {seats.map((s) => cell(s.label, t.models[s.key] ?? null, false, "This model gave no usable answer."))}
      {cell("Engine (price history)", t.engine, false, engineWhy)}
    </div>
  );
}

function Stocks({ t, seats, m, engineWhy }: { t: PanelTable; seats: PanelRun["seats"]; m: (v: number | null | undefined) => string; engineWhy: string }) {
  const wide = useMediaQuery("(min-width: 768px)");
  const [open, setOpen] = useState<string | null>(null);
  const rows = [...t.rows].sort((a, b) => (a.avg_amt ?? 0) - (b.avg_amt ?? 0));
  const flags = (r: PanelStock) => [r.split && "Models split", r.engine_gap && "Far from engine"].filter(Boolean).join(", ");
  const Reasons = ({ r }: { r: PanelStock }) => (
    <ul className="space-y-2 py-2 text-sm">
      {seats.map((s) => {
        const c = r.models[s.key];
        return (
          <li key={s.key} className="grid gap-x-4 gap-y-0.5 sm:grid-cols-[110px_150px_1fr]">
            <span className="font-medium">{s.label}</span>
            {c ? <><span className="num text-muted">{p1(c.low)} to {p1(c.high)}, {c.confidence} confidence</span><span dir="auto" className="text-muted">{c.reason}</span></>
              : <span className="text-faint sm:col-span-2">No estimate for this stock.</span>}
          </li>
        );
      })}
    </ul>
  );
  if (!wide) {
    return (
      <ul className="mt-6">
        {rows.map((r) => (
          <li key={r.symbol} className="border-t border-line py-3">
            <button type="button" className="flex w-full items-baseline justify-between gap-3 text-left" aria-expanded={open === r.symbol} onClick={() => setOpen(open === r.symbol ? null : r.symbol)}>
              <span><span className="font-mono font-semibold">{r.symbol}</span> <span className="text-xs text-muted">{pct(r.weight, 1, false)}</span></span>
              <span className="num">{r.avg != null ? <><Signed value={r.avg} className="font-semibold">{p1(r.avg)}</Signed> <Signed value={r.avg_amt} className="text-xs">{m(r.avg_amt)}</Signed></> : <Blank why="No model gave an estimate." />}</span>
            </button>
            <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted">
              {seats.map((s) => <span key={s.key}>{s.label} <span className="num text-text">{r.models[s.key] ? p1(r.models[s.key]!.move) : "none"}</span></span>)}
              <span>Engine <span className="num text-text">{r.engine != null ? p1(r.engine) : "none"}</span></span>
            </div>
            {flags(r) && <p className="mt-1 text-xs font-medium">{flags(r)}</p>}
            {open === r.symbol && <Reasons r={r} />}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="mt-6 overflow-x-auto">
      <table className="w-full border-separate border-spacing-0 text-sm">
        <thead><tr className="text-xs text-muted">
          <th className="border-b border-line py-2 text-left font-medium">Stock</th>
          <th className="border-b border-line py-2 pl-3 text-right font-medium">Weight</th>
          {seats.map((s) => <th key={s.key} className="border-b border-line py-2 pl-3 text-right font-medium">{s.label}</th>)}
          <th className="border-b border-line py-2 pl-3 text-right font-medium">Average</th>
          <th className="border-b border-line py-2 pl-3 text-right font-medium"><InfoTip text="The app's own factor model on the same question, from ten years of prices. Not an AI opinion.">Engine</InfoTip></th>
          <th className="border-b border-line py-2 pl-3 text-left font-medium">Note</th>
        </tr></thead>
        <tbody>{rows.map((r) => (
          <Fragment key={r.symbol}>
            <tr tabIndex={0} aria-expanded={open === r.symbol} onClick={() => setOpen(open === r.symbol ? null : r.symbol)} onKeyDown={(e) => { if (e.key === "Enter") setOpen(open === r.symbol ? null : r.symbol); }}
              className="cursor-pointer transition-colors duration-fast hover:bg-hover">
              <td className="border-b border-line py-2.5"><span className="flex items-center gap-1.5"><CaretDown size={12} className={clsx("text-faint transition-transform duration-fast", open === r.symbol && "rotate-180")} />
                <span className="font-mono font-semibold">{r.symbol}</span><span className="hidden truncate text-xs text-muted lg:inline">{r.name}</span></span></td>
              <td className="num border-b border-line py-2.5 pl-3 text-right text-muted">{pct(r.weight, 1, false)}</td>
              {seats.map((s) => <td key={s.key} className="num border-b border-line py-2.5 pl-3 text-right">{r.models[s.key] ? <Signed value={r.models[s.key]!.move}>{p1(r.models[s.key]!.move)}</Signed> : <Blank why="This model gave no estimate for this stock." />}</td>)}
              <td className="num border-b border-line py-2.5 pl-3 text-right">{r.avg != null ? <><Signed value={r.avg} className="font-semibold">{p1(r.avg)}</Signed> <Signed value={r.avg_amt} className="text-xs">{m(r.avg_amt)}</Signed></> : <Blank why="No model gave an estimate." />}</td>
              <td className="num border-b border-line py-2.5 pl-3 text-right">{r.engine != null ? <Signed value={r.engine}>{p1(r.engine)}</Signed> : <Blank why={engineWhy || "The engine has no figure for this stock."} />}</td>
              <td className="border-b border-line py-2.5 pl-3 text-xs font-medium">{flags(r)}</td>
            </tr>
            {open === r.symbol && <tr><td colSpan={seats.length + 5} className="border-b border-line bg-sunken px-4"><Reasons r={r} /></td></tr>}
          </Fragment>
        ))}</tbody>
      </table>
      <p className="mt-2 text-xs text-faint">Sorted by effect on this portfolio. Click a row for each model's range and reason.</p>
    </div>
  );
}

function Analysts({ d }: { d: PanelRun }) {
  const seats = d.seats.filter((s) => d.analysts?.[s.key]);
  const [who, setWho] = useState(seats[0]?.key ?? "");
  const a = d.analysts?.[who];
  if (!a) return null;
  return (
    <section className="mt-14" aria-labelledby="analysts-h">
      <h2 id="analysts-h" className="text-lg font-semibold">How each analyst worked</h2>
      <div className="mt-3"><Segmented label="Analyst" size="sm" value={who} onChange={setWho} options={seats.map((s) => ({ value: s.key, label: s.label }))} /></div>
      <p className="mt-2 text-xs text-faint">{seats.find((s) => s.key === who)?.model}</p>
      <div className="mt-4 max-w-[72ch] space-y-5 text-sm leading-6" dir="rtl" lang="he">
        {a.briefing && <div><h3 className="font-semibold">תקציר</h3><p className="mt-1 whitespace-pre-line">{a.briefing}</p></div>}
        {a.method && <div><h3 className="font-semibold">שיטת המחקר</h3><p className="mt-1 whitespace-pre-line text-muted">{a.method}</p></div>}
        {a.market_view && <div><h3 className="font-semibold">מבט על השוק</h3><p className="mt-1 whitespace-pre-line text-muted">{a.market_view}</p></div>}
        {a.assumptions.length > 0 && <div><h3 className="font-semibold">הנחות</h3><ul className="mt-1 list-disc space-y-1 pr-5 text-muted">{a.assumptions.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
      </div>
      {a.sources.length > 0 && (
        <div className="mt-5 max-w-[72ch] text-xs text-muted"><span className="font-medium text-text">Sources the model cited</span>
          <ul className="mt-1 space-y-0.5">{a.sources.map((x, i) => <li key={i} className="truncate">{/^https?:\/\//.test(x) ? <a className="link" href={x} target="_blank" rel="noreferrer">{x}</a> : x}</li>)}</ul></div>
      )}
    </section>
  );
}

function Board() {
  const q = useData(api.panelBoard, "panel-board");
  if (!q.data?.length) return null;
  return (
    <section className="mt-14" aria-labelledby="pboard-h">
      <h2 id="pboard-h" className="text-lg font-semibold">Which model has been closest</h2>
      <p className="mt-1 max-w-[70ch] text-sm text-muted">Once a question's time span has passed, each model's moves are compared with what prices did. Prices also move for other reasons, so read this as a rough guide.</p>
      <table className="mt-3 w-full max-w-2xl text-sm">
        <thead><tr className="text-xs text-muted"><th className="border-b border-line py-1.5 text-left font-medium">Model</th><th className="border-b border-line py-1.5 pl-3 text-right font-medium">Questions</th>
          <th className="border-b border-line py-1.5 pl-3 text-right font-medium">Right direction</th><th className="border-b border-line py-1.5 pl-3 text-right font-medium">Average miss</th><th className="border-b border-line py-1.5 pl-3 text-right font-medium">Inside its range</th></tr></thead>
        <tbody>{q.data.map((x) => (
          <tr key={x.key}><td className="border-b border-line py-1.5 font-medium">{x.label}</td><td className="num border-b border-line py-1.5 pl-3 text-right">{x.runs}</td>
            <td className="num border-b border-line py-1.5 pl-3 text-right">{x.direction_hit_rate != null ? pct(x.direction_hit_rate, 0, false) : <Blank why="No calls with a clear direction yet." />}</td>
            <td className="num border-b border-line py-1.5 pl-3 text-right">{x.avg_error.toFixed(1)} pts</td>
            <td className="num border-b border-line py-1.5 pl-3 text-right">{pct(x.in_range_rate, 0, false)}</td></tr>))}
        </tbody>
      </table>
    </section>
  );
}

const STARTERS = ["מי מהתיקים הכי חשוף ולמה?", "על אילו מניות המודלים הכי חלוקים, ומה כל אחד טוען?", "ומה אם זה נמשך חצי שנה?"];
const STARTERS_OPEN = ["מה הסיכון הכי גדול בתיקים כרגע?", "אילו דוחות חשובים לתיקים מתפרסמים השבוע?", "הריבית בארה\"ב יורדת ברבע אחוז בניגוד לציפיות. מה קורה?"];

/** The conversation with the assistant, about this result (or about the portfolios when there is none). */
function Chat({ runId, onAskPanel }: { runId: number; onAskPanel: (q: string) => void }) {
  // a follow-up on a result needs no web search (it costs extra); a fresh question about the news does
  const [web, setWeb] = useState(runId === 0);
  const [msgs, setMsgs] = useState<ChatMsg[] | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { let live = true; api.chat(runId).then((m) => { if (live) setMsgs(m); }).catch(() => { if (live) setMsgs([]); }); return () => { live = false; }; }, [runId]);
  const send = async (q: string) => {
    q = q.trim();
    if (!q || pending) return;
    setPending(q); setErr(null); setText("");
    setTimeout(() => end.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 50);
    try { setMsgs(await api.chatAsk(runId, { text: q, web })); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't get an answer."); setText(q); }
    finally { setPending(null); }
  };
  const last = msgs?.[msgs.length - 1];
  const chips = pending ? [] : last?.role === "assistant" && last.followups?.length ? last.followups : !msgs?.length ? (runId ? STARTERS : STARTERS_OPEN) : [];
  return (
    <section className="mt-14" aria-labelledby={`chat-h-${runId}`}>
      <h2 id={`chat-h-${runId}`} className="text-lg font-semibold">{runId ? "Ask a follow-up" : "Talk to the assistant"}</h2>
      <p className="mt-1 max-w-[70ch] text-sm text-muted">{runId ? "It answers from the numbers above and the engine." : "It knows what each portfolio holds."} Portfolio names are swapped for labels before anything is sent, and amounts are filled in here.</p>
      <ol className="mt-4 max-w-[72ch] space-y-5">
        {(msgs ?? []).map((m) => m.role === "user" ? (
          <li key={m.id} className="border-s-2 border-accent ps-3 text-sm font-medium" dir="auto">{m.text}</li>
        ) : (
          <li key={m.id} className="text-sm leading-6">
            <div dir="auto" className="whitespace-pre-line text-base leading-7">{m.text}</div>
            {m.caveat && <p dir="auto" className="mt-2 text-sm text-muted">{m.caveat}</p>}
            {m.engine && (
              <p className="mt-2 text-xs text-muted">Engine on this question ({m.engine.read_as.map((x) => `${x.symbol} ${p1(x.move_pct)}`).join(", ")}, over {m.engine.over_weeks} weeks):
                {" "}all portfolios <Signed value={m.engine.all_portfolios.typical_pct}>{p1(m.engine.all_portfolios.typical_pct)}</Signed>, worst of 20 <Signed value={m.engine.all_portfolios.worst_of_20_pct}>{p1(m.engine.all_portfolios.worst_of_20_pct)}</Signed>.</p>
            )}
            {m.cost != null && m.cost > 0 && <p className="mt-1 text-xs text-faint">This answer cost {money(m.cost, "USD", { decimals: 2 })}.</p>}
            {m.ask_panel && <button type="button" className="link mt-2 text-sm" onClick={() => { const q = msgs?.slice(0, msgs.indexOf(m)).reverse().find((x) => x.role === "user")?.text; if (q) onAskPanel(q); }}>Ask all three analysts this</button>}
          </li>
        ))}
        {pending && <>
          <li className="border-s-2 border-accent ps-3 text-sm font-medium" dir="auto">{pending}</li>
          <li className="flex items-center gap-2 text-sm text-muted" aria-live="polite"><span aria-hidden className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />Thinking{web ? " and checking the web" : ""}. This can take up to a minute.</li>
        </>}
      </ol>
      <div ref={end} />
      {err && <ErrorLine message={err} />}
      {chips.length > 0 && <div className="mt-4 flex flex-wrap gap-2">{chips.map((x) => <button key={x} type="button" dir="auto" className="chip h-auto min-h-7 py-1 text-left" onClick={() => send(x)}>{x}</button>)}</div>}
      <form className="mt-3 flex max-w-[72ch] flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <label htmlFor={`chat-q-${runId}`} className="sr-only">Your follow-up question</label>
        <input id={`chat-q-${runId}`} dir="auto" autoComplete="off" className="field h-10 flex-1" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about a stock, a portfolio, or another what-if" />
        <button type="submit" className="btn-secondary h-10" disabled={!text.trim() || !!pending}>Ask</button>
      </form>
      <label className="mt-2 flex cursor-pointer items-center gap-2 text-xs text-muted">
        <input type="checkbox" className="h-3.5 w-3.5 accent-[var(--accent)]" checked={web} onChange={(e) => setWeb(e.target.checked)} />
        Search the web for this answer (costs a little more)
      </label>
    </section>
  );
}
