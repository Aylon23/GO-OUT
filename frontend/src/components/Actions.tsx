import { Check, Copy, Lightbulb } from "@phosphor-icons/react";
import { useState } from "react";
import { api, ApiError, type Actions as A, type ActionsBody, type PersonActions } from "../lib/api";
import { amt, pct } from "../lib/format";
import { ErrorLine, Signed, Skel } from "./ui";

const fmt = (v: number | null | undefined, signed = false) => amt(v, signed);

/** What each person could do in the scenario on screen. Loads on request (it reruns the simulation). */
export function PersonActionsPanel({ body }: { body: ActionsBody }) {
  const [d, setD] = useState<A | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadedFor, setLoadedFor] = useState("");
  const key = JSON.stringify(body);
  const load = async () => {
    setBusy(true); setErr(null);
    try { setD(await api.actions(body)); setLoadedFor(key); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't work it out."); } finally { setBusy(false); }
  };
  const stale = d && loadedFor !== key;
  return (
    <section className="mt-12" aria-label="What each person could do">
      <h2 className="flex items-center gap-2 text-lg font-semibold"><Lightbulb size={18} className="text-accent" />What each person could do</h2>
      <p className="mt-1 max-w-[70ch] text-sm text-muted">Long-only options for each portfolio, each capped at half of it, with what it saves if the scenario happens and what it costs if it doesn't. Ideas to weigh, not advice.</p>
      {(!d || stale) && <button type="button" className="btn-secondary mt-4" disabled={busy} onClick={load}>{busy ? "Working it out" : stale ? "Update for these moves" : "Show options for each person"}</button>}
      {err && <ErrorLine message={err} />}
      {busy && !d && <div className="mt-4 space-y-2"><Skel className="h-24 w-full" /><Skel className="h-24 w-full" /></div>}
      {d && !d.empty && (
        <div className={stale ? "opacity-60" : ""}>
          <p className="mt-4 text-sm text-muted">
            Most exposed (average loss): {d.rank_expected.slice(0, 3).map((id) => d.people.find((p) => p.id === id)?.name).join(", ")}
            <span className="text-faint"> · worst bad case: {d.rank_bad.slice(0, 3).map((id) => d.people.find((p) => p.id === id)?.name).join(", ")}</span>
          </p>
          <ul className="mt-4 space-y-6">{d.rank_expected.map((id) => <Person key={id} p={d.people.find((x) => x.id === id)!} />)}</ul>
        </div>
      )}
    </section>
  );
}

function Person({ p }: { p: PersonActions }) {
  const [msg, setMsg] = useState(false);
  const [copied, setCopied] = useState(false);
  return (
    <li className="border-t border-line pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-base font-semibold">{p.name}</p>
        <p className="text-sm text-muted">Typical <Signed value={p.median}>{pct(p.median, 1)}</Signed> <Signed value={p.median_amt}>{fmt(p.median_amt, true)}</Signed>, worst of 20 <Signed value={p.bad}>{pct(p.bad, 1)}</Signed> <Signed value={p.bad_amt}>{fmt(p.bad_amt, true)}</Signed></p>
      </div>
      <p className="mt-1 text-sm text-muted">{p.line}</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[620px] border-separate border-spacing-0 text-sm">
          <thead><tr className="text-xs text-muted">
            <th className="border-b border-line py-1.5 text-left font-medium">Option</th>
            <th className="border-b border-line py-1.5 pl-3 text-right font-medium">Size</th>
            <th className="border-b border-line py-1.5 pl-3 text-right font-medium">Saves if it happens</th>
            <th className="border-b border-line py-1.5 pl-3 text-right font-medium">Costs {p.other_label}</th>
            <th className="border-b border-line py-1.5 pl-3 text-right font-medium">Largest holding</th>
          </tr></thead>
          <tbody>{p.options.map((o, i) => (
            <tr key={i}>
              <td className="border-b border-line py-2">{o.title}</td>
              <td className="num border-b border-line py-2 pl-3 text-right">{o.kind === "nothing" ? "–" : <>{pct(o.share_of_portfolio, 0, false)} <span className="text-xs text-faint">{fmt(o.amount)}</span></>}</td>
              <td className="num border-b border-line py-2 pl-3 text-right">{o.kind === "nothing" ? "–" : <Signed value={o.benefit_amt}>{fmt(o.benefit_amt, true)}</Signed>}</td>
              <td className="num border-b border-line py-2 pl-3 text-right">{o.kind === "nothing" ? "–" : o.cost_amt > 0.5 ? <Signed value={-o.cost_amt}>{fmt(-o.cost_amt, true)}</Signed> : <span className="text-faint">none</span>}</td>
              <td className="num border-b border-line py-2 pl-3 text-right text-muted">{pct(o.top_weight_before, 0, false)}{o.kind !== "nothing" && Math.abs(o.top_weight_after - o.top_weight_before) > 0.005 && <> → <span className="text-text">{pct(o.top_weight_after, 0, false)}</span></>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <button type="button" className="btn-ghost -ml-2 mt-2" onClick={() => setMsg(!msg)}>{msg ? "Hide message" : "Draft message"}</button>
      {msg && (
        <div className="mt-2 max-w-[640px]">
          <textarea dir="rtl" readOnly className="field h-56 py-2 text-sm leading-6" value={p.message} aria-label={`Message draft for ${p.name}`} />
          <div className="mt-2 flex items-center gap-3">
            <button type="button" className="btn-secondary h-8" onClick={async () => { try { await navigator.clipboard.writeText(p.message); setCopied(true); setTimeout(() => setCopied(false), 1600); } catch { /* select manually */ } }}>
              {copied ? <><Check size={14} />Copied</> : <><Copy size={14} />Copy</>}
            </button>
            <span className="text-xs text-faint">Nothing is sent. Copy it into WhatsApp or anywhere you like.</span>
          </div>
        </div>
      )}
    </li>
  );
}
