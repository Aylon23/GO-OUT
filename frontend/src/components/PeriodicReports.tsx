import { FilePdf, ImageSquare, PaperPlaneTilt } from "@phosphor-icons/react";
import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { dateLong } from "../lib/format";
import { useData } from "../lib/hooks";
import { ErrorLine } from "./ui";

/** Weekly and monthly report cards (image + PDF) per person and for the family. For me only. */
export function PeriodicReports() {
  const q = useData(api.reportsList, "reports-list");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const make = async (kind: "weekly" | "monthly", send: boolean) => {
    setBusy(kind + send); setErr(null); setMsg(null);
    try {
      const r = await api.reportsGenerate(kind, send);
      setMsg(`Made ${r.made} report${r.made === 1 ? "" : "s"} for ${dateLong(r.period_start)} – ${dateLong(r.period_end)}.` + (send ? (r.send_errors.length ? ` Not sent: ${r.send_errors[0]}` : " Sent to your Telegram.") : ""));
      q.reload();
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't make them."); } finally { setBusy(null); }
  };
  const groups = new Map<string, NonNullable<typeof q.data>>();
  for (const r of q.data ?? []) {
    const k = `${r.kind}|${r.period_start}|${r.period_end}`;
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  return (
    <section className="mt-16" aria-label="Periodic reports">
      <h2 className="text-lg font-semibold">Reports</h2>
      <p className="mt-1 max-w-[72ch] text-sm text-muted">A phone-sized card and a short PDF for each person and for the family, made every Sunday (last week) and on the 1st (last month) while the app runs. They go only to you: here, and to your Telegram chat if it's connected. Forward them yourself.</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => make("weekly", false)}>{busy === "weeklyfalse" ? "Making" : "Make last week's"}</button>
        <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => make("monthly", false)}>{busy === "monthlyfalse" ? "Making" : "Make last month's"}</button>
        <button type="button" className="btn-ghost" disabled={!!busy} onClick={() => make("weekly", true)}><PaperPlaneTilt size={14} />{busy === "weeklytrue" ? "Sending" : "Make last week's and send to me"}</button>
      </div>
      {msg && <p className="mt-2 text-sm text-muted" role="status">{msg}</p>}
      {err && <ErrorLine message={err} />}
      {[...groups.entries()].slice(0, 6).map(([k, rows]) => {
        const [kind, s, e] = k.split("|");
        return (
          <div key={k} className="mt-6">
            <p className="text-sm font-medium">{kind === "weekly" ? "Week" : "Month"} of {dateLong(s)} – {dateLong(e)}</p>
            <ul className="mt-2 border-t border-line text-sm">{rows.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line py-2">
                <span className="min-w-[140px] font-medium" dir="auto">{r.portfolio_id == null ? "Family summary" : r.name}</span>
                <a className="btn-ghost h-7" href={`/api/reports/file/${r.id}.png`} target="_blank" rel="noreferrer"><ImageSquare size={14} />Card</a>
                <a className="btn-ghost h-7" href={`/api/reports/file/${r.id}.pdf`} target="_blank" rel="noreferrer"><FilePdf size={14} />PDF</a>
                <button type="button" className="btn-ghost h-7" onClick={async () => { try { await api.reportSend(r.id); q.reload(); } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't send."); } }}>
                  <PaperPlaneTilt size={14} />{r.sent_at ? "Send again" : "Send to me"}</button>
                {r.sent_at && <span className="text-xs text-faint">sent {dateLong(r.sent_at.slice(0, 10))}</span>}
              </li>
            ))}</ul>
          </div>
        );
      })}
    </section>
  );
}
