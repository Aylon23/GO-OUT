import { ArrowLeft, ArrowRight, CheckCircle, DownloadSimple, Tray, Warning, XCircle } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { api, ApiError, type ImpactEdit, type ImpactResult, type ScenarioSpec, type SpecReport } from "../lib/api";
import { dateLong } from "../lib/format";
import { useData } from "../lib/hooks";
import { PersonActionsPanel } from "./Actions";
import { MemoPanel } from "./Memo";
import { Impact, type RegimeChoice } from "./Impact";
import { EmptyState, ErrorLine, Signed, Skel } from "./ui";

export const FACTOR_LABEL: Record<string, string> = {
  market: "Market (S&P 500)", size: "Size (small vs large)", value: "Value vs growth", momentum: "Momentum", quality: "Quality",
  rates: "Long bonds (TLT)", oil: "Oil", gold: "Gold",
};
const HORIZON: Record<string, string> = { "1w": "1 week", "2w": "2 weeks", "1m": "1 month", "3m": "3 months", "6m": "6 months", "12m": "12 months" };
const p1 = (v: number | null | undefined) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(Math.abs(v) < 10 ? 1 : 0)}%`);

export function Researched() {
  const list = useData(api.specs, "specs");
  const inbox = useData(api.inbox, "inbox");
  const [importing, setImporting] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [checking, setChecking] = useState<string | null>(null);

  if (openId) return <SpecDetail id={openId} onBack={() => { setOpenId(null); list.reload(); }} />;
  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-[64ch] text-sm text-muted">Scenarios researched outside the app (in a Claude chat or a scheduled task), imported here. The app checks them against real price history and never changes their numbers.</p>
        <button type="button" className="btn-primary" onClick={() => setImporting(true)}><DownloadSimple size={16} />Import researched scenario</button>
      </div>
      <ModelAccuracy />
      {importing && <ImportPanel onClose={() => setImporting(false)} onSaved={(id) => { setImporting(false); list.reload(); setOpenId(id); }} />}
      <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <Tray size={14} />
        <span>Inbox folder: <span className="select-all font-mono text-text">{inbox.data?.path ?? "…"}</span></span>
        <button type="button" className="link text-xs" onClick={async () => {
          const r = await api.inboxCheck();
          setChecking(r.length ? r.map((x) => `${x.file}: ${x.ok ? "imported" : "failed"}`).join(" · ") : "No new files.");
          list.reload(); inbox.reload();
        }}>Check now</button>
        {checking && <span className="text-faint">{checking}</span>}
      </div>
      {inbox.data?.failed.length ? (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-loss">{inbox.data.failed.length} file{inbox.data.failed.length === 1 ? "" : "s"} failed recently</summary>
          <ul className="mt-1 space-y-2">{inbox.data.failed.map((f) => <li key={f.file}><span className="font-mono">{f.file}</span><pre className="mt-0.5 whitespace-pre-wrap text-muted">{f.error}</pre></li>)}</ul>
        </details>
      ) : null}
      {list.error && <ErrorLine message={list.error.message} onRetry={list.reload} />}
      {!list.data ? <div className="mt-6 space-y-2"><Skel className="h-12 w-full" /><Skel className="h-12 w-full" /></div>
        : list.data.length === 0 ? <EmptyState title="No researched scenarios yet." body="Ask Claude for a Scenario Spec (see docs/scenario-spec.schema.json), then paste it here or drop the .json file into the inbox folder." />
          : (
            <ul className="mt-6 border-t border-line">
              {list.data.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => setOpenId(r.id)} className="press flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line py-3 text-left hover:bg-hover">
                    <span className="min-w-0">
                      <span className="block font-medium">{r.title}</span>
                      {r.title_he && <span className="block text-sm text-muted" dir="rtl">{r.title_he}</span>}
                    </span>
                    <span className="flex flex-wrap items-center gap-x-3 text-xs text-muted">
                      <span>{HORIZON[r.horizon] ?? r.horizon}</span><span>{r.branches} branch{r.branches === 1 ? "" : "es"}</span>
                      <span>{r.confidence} confidence</span>
                      {r.warnings > 0 && <span className="text-text">{r.warnings} warning{r.warnings === 1 ? "" : "s"}</span>}
                      <span className="text-faint">{r.source === "inbox" ? "from inbox" : "pasted"} {dateLong(r.imported_at.slice(0, 10))}</span>
                      <ArrowRight size={14} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
    </section>
  );
}

function ImportPanel({ onClose, onSaved }: { onClose: () => void; onSaved: (id: string) => void }) {
  const [text, setText] = useState("");
  const [res, setRes] = useState<{ spec: ScenarioSpec; report: SpecReport } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await api.specValidate(text)); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't read it."); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setErr(null);
    try { const r = await api.specSave(text); onSaved(r.id); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); } finally { setBusy(false); }
  };
  return (
    <div className="fade-in mt-6 rounded-md border border-line bg-surface p-4">
      <label htmlFor="spec-text" className="label">Paste the researched scenario</label>
      <textarea id="spec-text" dir="auto" className="field h-48 py-2 font-mono text-xs" value={text} onChange={(e) => { setText(e.target.value); setRes(null); }}
        placeholder={'The JSON spec, or the whole message that contains it (a ```json block is fine).'} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn-secondary" disabled={!text.trim() || busy} onClick={check}>{busy && !res ? "Checking" : "Check"}</button>
        <button type="button" className="btn-primary" disabled={!res || !res.report.ok || busy} onClick={save}>{res && !res.report.ok ? "Fix the errors to save" : "Save"}</button>
        <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
      </div>
      {err && <p className="error-text" role="alert">{err}</p>}
      {res && <div className="mt-4"><p className="font-medium">{res.spec.title}</p><Report report={res.report} /></div>}
    </div>
  );
}

export function Report({ report }: { report: SpecReport }) {
  return (
    <div className="mt-2 space-y-3 text-sm">
      {report.errors.length === 0
        ? <p className="flex items-center gap-2 text-gain"><CheckCircle size={16} />No errors{report.warnings.length ? `, ${report.warnings.length} warning${report.warnings.length === 1 ? "" : "s"}` : ""}.</p>
        : <div><p className="flex items-center gap-2 font-medium text-loss"><XCircle size={16} />{report.errors.length} error{report.errors.length === 1 ? "" : "s"}, can't save</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-6 text-loss">{report.errors.map((e) => <li key={e}>{e}</li>)}</ul></div>}
      {report.warnings.length > 0 && <div><p className="flex items-center gap-2 font-medium"><Warning size={16} className="text-accent" />Warnings</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-6 text-muted">{report.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>}
    </div>
  );
}

function SpecDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const q = useData(() => api.spec(id), `spec-${id}`);
  const [confirm, setConfirm] = useState(false);
  const r = q.data;
  if (q.error) return <ErrorLine message={q.error.message} onRetry={q.reload} />;
  if (!r || !r.spec || !r.report) return <div className="mt-8 space-y-3"><Skel className="h-8 w-2/3" /><Skel className="h-40 w-full" /></div>;
  const s = r.spec;
  const factors = Array.from(new Set(s.branches.flatMap((b) => Object.keys(b.factor_moves))));
  const sectors = Array.from(new Set(s.branches.flatMap((b) => Object.keys(b.sector_overrides ?? {}))));
  return (
    <article className="mt-6">
      <button type="button" className="btn-ghost -ml-2" onClick={onBack}><ArrowLeft size={14} />All researched scenarios</button>
      <h2 className="mt-3 text-2xl font-semibold">{s.title}</h2>
      {s.title_he && <p className="mt-1 text-lg text-muted" dir="rtl">{s.title_he}</p>}
      <p className="mt-2 flex flex-wrap gap-x-3 text-xs text-faint">
        <span>Over {HORIZON[s.horizon] ?? s.horizon}</span><span>{s.confidence} confidence</span><span>by {s.author}, {dateLong(s.created_at.slice(0, 10))}</span>
        {s.question && <span dir="auto">Asked: “{s.question}”</span>}
      </p>
      <p className="mt-4 max-w-[72ch] text-base">{s.summary}</p>
      <SpecImpact id={id} />

      <h3 className="mt-10 text-base font-semibold">Branches</h3>
      <ul className="mt-3 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {s.branches.map((b) => (
          <li key={b.name} className="rounded-md border border-line p-4">
            <div className="flex items-baseline justify-between gap-3"><p className="font-medium">{b.name}</p><p className="num text-xl font-semibold">{Math.round(b.probability * 100)}%</p></div>
            <p className="text-xs text-faint">{b.cause}</p>
            <p className="mt-2 text-sm text-muted">{b.narrative}</p>
            {b.transmission?.length ? (
              <ul className="mt-3 space-y-1 text-xs text-muted">{b.transmission.map((t, i) => (
                <li key={i}><span className="text-text">{t.from === "trigger" ? "Trigger" : FACTOR_LABEL[t.from] ?? t.from}</span> → <span className="text-text">{FACTOR_LABEL[t.to] ?? t.to}</span> <span className={t.direction === "+" ? "text-gain" : "text-loss"}>{t.direction === "+" ? "up" : "down"}</span>{t.explanation && `: ${t.explanation}`}</li>
              ))}</ul>
            ) : null}
          </li>
        ))}
      </ul>

      <h3 className="mt-10 text-base font-semibold">Factor moves</h3>
      <p className="mt-1 text-xs text-faint">Middle estimate, with the low to high range, over {HORIZON[s.horizon] ?? s.horizon}.</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[480px] border-separate border-spacing-0 text-sm">
          <thead><tr>
            <th className="border-b border-line py-2 text-left text-xs font-medium text-muted">Factor</th>
            {s.branches.map((b) => <th key={b.name} className="border-b border-line py-2 pl-3 text-right text-xs font-medium text-muted">{b.name} <span className="text-faint">{Math.round(b.probability * 100)}%</span></th>)}
          </tr></thead>
          <tbody>
            {[...factors.map((f) => ["factor", f] as const), ...sectors.map((x) => ["sector", x] as const)].map(([kind, f]) => (
              <tr key={f}>
                <td className="border-b border-line py-2">{kind === "factor" ? FACTOR_LABEL[f] ?? f : <span>Sector <span className="font-mono">{f}</span></span>}</td>
                {s.branches.map((b) => {
                  const m = kind === "factor" ? b.factor_moves[f] : b.sector_overrides?.[f];
                  return <td key={b.name} className="num border-b border-line py-2 pl-3 text-right" title={m?.rationale}>
                    {m ? <><Signed value={m.mid}>{p1(m.mid)}</Signed><span className="block text-xs text-faint">{p1(m.low)} to {p1(m.high)}</span></> : <span className="text-faint">–</span>}
                  </td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="mt-10 text-base font-semibold">Past analogs, checked against our price data</h3>
      {r.report.analogs.length === 0 ? <p className="mt-2 text-sm text-muted">No analogs given.</p> : (
        <ul className="mt-3 grid gap-x-8 gap-y-5 md:grid-cols-2">
          {r.report.analogs.map((a, i) => (
            <li key={i} className="border-t border-line pt-3">
              <p className="font-medium">{a.name} <span className="text-xs font-normal text-faint">{a.branch}</span></p>
              <p className="text-xs text-faint">{dateLong(a.start)} to {dateLong(a.end)}{a.why_similar && ` · ${a.why_similar}`}</p>
              <table className="mt-2 w-full text-xs">
                <thead><tr className="text-faint"><th className="py-1 text-left font-normal">Factor</th><th className="py-1 text-right font-normal">Branch says</th><th className="py-1 text-right font-normal">Actually moved</th></tr></thead>
                <tbody>{a.factors.map((f) => (
                  <tr key={f.factor} className={clsx(f.contradicts && "font-medium")}>
                    <td className="py-0.5">{FACTOR_LABEL[f.factor] ?? f.factor}</td>
                    <td className="num py-0.5 text-right">{p1(f.claimed_mid)}</td>
                    <td className="num py-0.5 text-right">{f.actual == null ? <span className="text-faint">no data</span> : <><Signed value={f.actual}>{p1(f.actual)}</Signed>{f.contradicts && <Warning size={12} className="ml-1 inline text-accent" aria-label="contradicts the branch" />}</>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </li>
          ))}
        </ul>
      )}

      {s.branches.some((b) => b.signposts?.length) && (
        <>
          <h3 className="mt-10 text-base font-semibold">Signposts to watch</h3>
          <ul className="mt-3 border-t border-line text-sm">
            {s.branches.flatMap((b) => (b.signposts ?? []).map((sp, i) => (
              <li key={`${b.name}-${i}`} className="flex flex-wrap justify-between gap-x-4 border-b border-line py-2">
                <span><span className="font-medium">{sp.indicator}</span>{sp.symbol && <span className="ml-1.5 font-mono text-xs text-faint">{sp.symbol}</span>} <span className="num">{sp.condition} {sp.value}</span></span>
                <span className="text-muted">{sp.meaning} <span className="text-xs text-faint">({b.name})</span></span>
              </li>
            )))}
          </ul>
        </>
      )}

      {s.current_context?.length ? (
        <>
          <h3 className="mt-10 text-base font-semibold">Context when researched</h3>
          <ul className="mt-2 text-sm text-muted">{s.current_context.map((c) => <li key={c.item}>{c.item}: <span className="text-text">{c.value}</span> <span className="text-xs text-faint">as of {dateLong(c.as_of)}</span></li>)}</ul>
        </>
      ) : null}
      {s.what_would_change_my_view && <p className="mt-6 max-w-[72ch] text-sm"><span className="font-medium">What would change the view: </span><span className="text-muted">{s.what_would_change_my_view}</span></p>}
      {s.sources?.length ? (
        <>
          <h3 className="mt-10 text-base font-semibold">Sources</h3>
          <ul className="mt-2 space-y-1 text-sm">{s.sources.map((x) => <li key={x.url}><a className="link" href={x.url} target="_blank" rel="noreferrer noopener">{x.title}</a>{x.date && <span className="ml-2 text-xs text-faint">{dateLong(x.date)}</span>}</li>)}</ul>
        </>
      ) : null}

      <h3 className="mt-10 text-base font-semibold">Validation</h3>
      <Report report={r.report} />
      <p className="mt-2 text-xs text-faint">Checked {dateLong(r.report.checked_at.slice(0, 10))}.</p>
      <div className="mt-8">
        {confirm ? (
          <span className="flex items-center gap-2 text-sm">Delete this scenario?
            <button type="button" className="btn-ghost" onClick={() => setConfirm(false)}>Keep</button>
            <button type="button" className="btn-secondary text-loss" onClick={async () => { await api.specDelete(id); onBack(); }}>Delete</button></span>
        ) : <button type="button" className="btn-ghost -ml-2 text-muted" onClick={() => setConfirm(true)}>Delete</button>}
      </div>
    </article>
  );
}

export function SpecImpact({ id }: { id: string }) {
  const [regime, setRegime] = useState<RegimeChoice>("auto");
  const [edits, setEdits] = useState<ImpactEdit[]>([]);
  const [r, setR] = useState<ImpactResult | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    setBusy(true); setErr(null);
    api.impactSpec(id, { regime: regime === "auto" ? null : regime, edits }, ctl.signal)
      .then((x) => { setR(x); setBusy(false); })
      .catch((e) => { if (e.name !== "AbortError") { setErr(e instanceof ApiError ? e.message : "Couldn't run it."); setBusy(false); } });
    return () => ctl.abort();
  }, [id, regime, edits]);
  if (err) return <ErrorLine message={err} />;
  if (!r) return <div className="mt-10 space-y-3" aria-label="Running"><Skel className="h-9 w-3/4" /><Skel className="h-4 w-1/2" /><Skel className="h-32 w-full" /></div>;
  if (r.empty) return <p className="mt-8 text-sm text-muted">Add holdings to see the impact on your portfolios.</p>;
  return (
    <>
      <Impact r={r} regime={regime} setRegime={setRegime} busy={busy}
        onEdit={(e) => setEdits((xs) => [...xs.filter((x) => !(x.branch === e.branch && x.factor === e.factor)), e])} />
      <PersonActionsPanel body={{ spec_id: id, regime: regime === "auto" ? null : regime, edits }} />
      <MemoPanel body={{ spec_id: id, regime: regime === "auto" ? null : regime, edits }} />
    </>
  );
}

function ModelAccuracy() {
  const q = useData(api.scenarioJournal, "scenario-journal");
  const d = q.data;
  if (!d || (d.rows.length === 0)) return null;
  const s = d.summary;
  return (
    <div className="mt-6 rounded-md border border-line p-4 text-sm">
      <p className="font-medium">Model accuracy</p>
      {s ? (
        <p className="mt-1 text-muted">
          {s.n} scenario{s.n === 1 ? "" : "s"} scored: typical miss for the family {(s.mae * 100).toFixed(1)}%, and {Math.round(s.coverage * 100)}% of outcomes inside the 5–95% band
          (aim: about {Math.round(d.target * 100)}%).{s.worst[0] && <> Worst miss: {s.worst[0].title}.</>}
        </p>
      ) : <p className="mt-1 text-muted">Nothing scored yet: each imported scenario is checked when its horizon ends.</p>}
      {d.pending > 0 && <p className="mt-1 text-xs text-faint">{d.pending} waiting for their horizon to end.</p>}
      {Math.abs(d.band_scale - 1) > 1e-9 && <p className="mt-2 text-xs font-medium">Ranges are now {d.band_scale.toFixed(2)}× wider: {d.band_note}</p>}
    </div>
  );
}
