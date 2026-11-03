import { useEffect, useState } from "react";
import { api, ApiError, type AiRole, type AiStatus } from "../lib/api";
import { useData } from "../lib/hooks";
import { AiQuota } from "./AiQuota";
import { ContextPackView } from "./ContextPackView";
import { ErrorLine, Segmented, SidePanel, Skel } from "./ui";

/** Settings → AI: key status, free-only toggle, model roster, quota, call log, queued jobs, context pack. */
export function AiSettings() {
  const st = useData(api.aiStatus, "ai-status");
  const models = useData(api.aiModels, "ai-models");
  const [s, setS] = useState<AiStatus | null>(null);
  const [roster, setRoster] = useState<AiRole[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [showPack, setShowPack] = useState(false);
  useEffect(() => { if (st.data) { setS(st.data); setRoster(st.data.roster); } }, [st.data]);
  if (st.error && !s) return <ErrorLine message={st.error.message} onRetry={st.reload} />;
  if (!s || !roster) return <Skel className="h-40 w-full" />;
  const save = async (b: Parameters<typeof api.aiSave>[0]) => {
    setErr(null); setSaved(false);
    try { const n = await api.aiSave(b); setS(n); setRoster(n.roster); setSaved(true); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); }
  };
  const setModel = (ri: number, mi: number, v: string) => setRoster(roster.map((r, i) => i !== ri ? r : { ...r, models: [0, 1, 2].map((k) => k === mi ? v : r.models[k] ?? "") }));
  const nonFree = s.free_only ? roster.flatMap((r) => r.models).filter((m) => m && !m.endsWith(":free")) : [];
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">{s.key_set ? <>OpenRouter key <span className="font-mono">{s.key_hint}</span> is set (from <span className="font-mono">.env</span> or the environment).</>
          : <>No key yet. Add <span className="font-mono">OPENROUTER_API_KEY=…</span> to the <span className="font-mono">.env</span> file next to the app and restart it.</>}</p>
        <AiQuota />
      </div>
      <div>
        <Segmented label="Free models only" value={s.free_only ? "on" : "off"} onChange={(v) => save({ free_only: v === "on" })}
          options={[{ value: "on", label: "Free models only" }, { value: "off", label: "Allow paid models" }]} />
        <p className="mt-1.5 text-xs text-muted">{s.free_only ? "Any model id without :free is refused before anything is sent." : "Paid models may cost money on your OpenRouter account."}</p>
      </div>
      <div>
        <p className="text-sm font-medium">Models</p>
        <p className="mt-0.5 text-xs text-muted">Four analysts from different labs and one judge. Each has two fallbacks, used by OpenRouter when the first is busy or down.</p>
        <datalist id="free-models">{(models.data?.models ?? []).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</datalist>
        <div className="mt-3 space-y-3">
          {roster.map((r, ri) => (
            <fieldset key={r.role} className="grid gap-1.5 sm:grid-cols-[88px_1fr]">
              <legend className="sr-only">{r.label}</legend>
              <span className="pt-1.5 text-sm text-muted">{r.label}</span>
              <div className="grid gap-1.5">
                {[0, 1, 2].map((mi) => (
                  <input key={mi} list="free-models" aria-label={`${r.label} ${mi === 0 ? "model" : `fallback ${mi}`}`} spellCheck={false}
                    placeholder={mi === 0 ? "model id" : `fallback ${mi}`} value={r.models[mi] ?? ""} onChange={(e) => setModel(ri, mi, e.target.value.trim())}
                    className={"field h-8 font-mono text-xs " + (mi ? "opacity-90" : "")} />
                ))}
              </div>
            </fieldset>
          ))}
        </div>
        {nonFree.length > 0 && <p className="mt-2 text-xs text-loss">Not free: {nonFree.join(", ")}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" className="btn-secondary" disabled={nonFree.length > 0} onClick={() => save({ roster })}>Save models</button>
          <button type="button" className="btn-ghost" onClick={() => save({ reset_roster: true })}>Reset to defaults</button>
          {saved && <span className="text-xs text-muted">Saved.</span>}
          <span className="text-xs text-faint">{models.data ? (models.data.live ? `${models.data.models.length} free models on OpenRouter today` : "OpenRouter's model list is unreachable; showing known free models") : ""}</span>
        </div>
        {err && <ErrorLine message={err} />}
      </div>
      <p className="text-xs text-muted">Pacing: at most {s.pace_per_min} requests a minute, one retry per request (failed requests count). Answers must be JSON matching a schema; an invalid answer gets one repair request. Requests carry tickers, weights and factor exposures only: never names or amounts.</p>
      <Jobs jobs={s.jobs} onResume={async (id) => { await api.aiResume(id); st.reload(); }} />
      <CallLog />
      <div>
        <button type="button" className="btn-secondary" onClick={() => setShowPack(true)}>Show the context pack</button>
        <p className="mt-1 text-xs text-muted">What the models are given, exactly as it would be sent.</p>
        <SidePanel open={showPack} onClose={() => setShowPack(false)} title="Context pack (as sent)" wide="xl">
          {showPack && <ContextPackView />}
        </SidePanel>
      </div>
    </div>
  );
}

function Jobs({ jobs, onResume }: { jobs: AiStatus["jobs"]; onResume: (id: number) => void }) {
  if (!jobs.length) return <p className="text-xs text-faint">No queued AI jobs. Long jobs wait for the next day when today's free requests run out, and never repeat finished calls.</p>;
  return (
    <div>
      <p className="text-sm font-medium">Queued jobs</p>
      <ul className="mt-2 divide-y divide-line text-sm">{jobs.map((j) => (
        <li key={j.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <span>{j.title || j.kind} <span className="num text-xs text-muted">· {j.done} of {j.total} done{j.failed ? `, ${j.failed} failed` : ""} · {j.status.replace("_", " ")}</span>
            {j.note && <span className="block text-xs text-faint">{j.note}</span>}</span>
          {["waiting_quota", "queued"].includes(j.status) && <button type="button" className="btn-ghost h-7 text-xs" onClick={() => onResume(j.id)}>Try now</button>}
        </li>
      ))}</ul>
    </div>
  );
}

function CallLog() {
  const q = useData(api.aiCalls, "ai-calls");
  const rows = q.data ?? [];
  return (
    <div>
      <p className="text-sm font-medium">Call log</p>
      {rows.length === 0 ? <p className="mt-1 text-xs text-faint">No AI calls yet.</p> : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[640px] text-xs">
            <thead><tr className="text-left text-muted">
              {["When", "Role", "Model", "Status", "Requests", "Tokens in/out", "Time", "Payload"].map((h) => <th key={h} className="border-b border-line py-1.5 pr-3 font-medium">{h}</th>)}
            </tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} title={r.error || undefined}>
                <td className="num border-b border-line py-1.5 pr-3">{new Date(r.at).toLocaleString()}</td>
                <td className="border-b border-line py-1.5 pr-3">{r.role}</td>
                <td className="border-b border-line py-1.5 pr-3 font-mono">{r.model_used || r.models[0]}</td>
                <td className={"border-b border-line py-1.5 pr-3 " + (r.status === "ok" ? "" : "text-loss")}>{r.status}</td>
                <td className="num border-b border-line py-1.5 pr-3">{r.requests}</td>
                <td className="num border-b border-line py-1.5 pr-3">{r.prompt_tokens.toLocaleString()} / {r.completion_tokens.toLocaleString()}</td>
                <td className="num border-b border-line py-1.5 pr-3">{(r.latency_ms / 1000).toFixed(1)}s</td>
                <td className="border-b border-line py-1.5 pr-3 font-mono">{r.payload_hash.slice(0, 10)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
