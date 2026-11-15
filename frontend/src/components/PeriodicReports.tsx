import { FilePdf, ImageSquare, PaperPlaneTilt } from "@phosphor-icons/react";
import { useState } from "react";
import { api, ApiError, type MarketWeek } from "../lib/api";
import { useApp } from "../state";
import { dateLong } from "../lib/format";
import { useData } from "../lib/hooks";
import { ErrorLine, Signed } from "./ui";

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
    <section aria-label="Periodic reports">
      <p className="max-w-[72ch] text-sm text-muted">A phone-sized card and a short PDF for each person and for the family, made every Sunday (last week) and on the 1st (last month) while the app runs. They go only to you: here, and to your Telegram chat if it's connected. Forward them yourself.</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => make("weekly", false)}>{busy === "weeklyfalse" ? "Making" : "Make last week's"}</button>
        <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => make("monthly", false)}>{busy === "monthlyfalse" ? "Making" : "Make last month's"}</button>
        <button type="button" className="btn-ghost" disabled={!!busy} onClick={() => make("weekly", true)}><PaperPlaneTilt size={14} />{busy === "weeklytrue" ? "Sending" : "Make last week's and send to me"}</button>
      </div>
      {msg && <p className="mt-2 text-sm text-muted" role="status">{msg}</p>}
      {err && <ErrorLine message={err} />}
      <MarketSummary />
      {[...groups.entries()].slice(0, 6).map(([k, rows]) => {
        const [kind, s, e] = k.split("|");
        return (
          <div key={k} className="mt-6">
            <p className="text-sm font-medium">{kind === "weekly" ? "Week" : "Month"} of {dateLong(s)} – {dateLong(e)}</p>
            <ul className="mt-2 border-t border-line text-sm">{rows.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line py-2">
                <span className="min-w-[140px] font-medium" dir="auto">{r.portfolio_id == null ? "Family summary" : r.portfolio_id === 0 ? "The week in the markets" : r.name}</span>
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


const signedPct = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;

/** The weekly market summary in plain Hebrew that goes with the weekly cards: its switch, and the latest one. */
function MarketSummary() {
  const { settings, saveSettings } = useApp();
  const q = useData(api.marketWeek, "market-week");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const on = settings?.market_summary ?? true;
  const m: MarketWeek | null = q.data?.summary ?? null;
  const written = !!m && m.week_end === q.data?.last_week_end && m.status === "done";
  const make = async () => {
    setBusy(true); setErr(null);
    try { await api.marketWeekMake(written); q.reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't write it."); } finally { setBusy(false); }
  };
  return (
    <div className="mt-8 border-t border-line pt-5">
      <label className="flex cursor-pointer items-start gap-3 text-sm">
        <input type="checkbox" className="mt-1 h-4 w-4 accent-[var(--accent)]" checked={on} onChange={(e) => saveSettings({ market_summary: e.target.checked })} />
        <span><span className="font-medium">The week in the markets</span>
          <span className="block text-muted">A short card in plain Hebrew with the weekly reports: how the indices, the dollar, oil and gold moved (computed from prices) and what drove them (written by one AI model with a web search; every number it writes is checked). One paid call a week, about 10 to 15 cents. Nothing about the portfolios is sent.</span></span>
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        <button type="button" className="btn-secondary" disabled={busy} onClick={make}>{busy ? "Writing" : written ? "Write last week's again" : "Write last week's now"}</button>
        {m && <span className="text-xs text-faint">Week ending {dateLong(m.week_end)}{m.cost ? `, cost $${m.cost.toFixed(2)}` : ""}</span>}
      </div>
      {err && <ErrorLine message={err} />}
      {m && (
        <div className="mt-4 max-w-[70ch] text-sm leading-6" dir="rtl" lang="he">
          {m.text.headline && <p className="text-base font-semibold">{m.text.headline}</p>}
          <ul className="mt-2 grid grid-cols-[1fr_auto] gap-x-6 gap-y-0.5">
            {m.facts.map((f) => (
              <li key={f.symbol} className="contents">
                <span>{f.name}</span>
                <span className="num text-left" dir="ltr">{f.change_pct != null
                  ? (f.kind === "rate" ? signedPct(f.change_pct) : <Signed value={f.change_pct}>{signedPct(f.change_pct)}</Signed>)
                  : `${(f.change_pts ?? 0) > 0 ? "+" : ""}${(f.change_pts ?? 0).toFixed(2)} pts`}</span>
              </li>
            ))}
          </ul>
          {m.status === "done" ? (
            <>
              {m.text.what_happened && <p className="mt-3">{m.text.what_happened}</p>}
              {(m.text.drivers ?? []).map((d) => <p key={d.title} className="mt-2"><span className="font-semibold">{d.title}: </span><span className="text-muted">{d.text}</span></p>)}
              {m.text.next_week && <p className="mt-2">בשבוע הבא: {m.text.next_week}</p>}
              {m.dropped.length > 0 && <p className="mt-2 text-xs text-faint" dir="ltr">{m.dropped.length} sentence{m.dropped.length === 1 ? "" : "s"} left out: {m.dropped.length === 1 ? "it" : "they"} quoted a number the app could not confirm.</p>}
            </>
          ) : <p className="mt-3 text-muted" dir="ltr">Only the numbers this week: {m.note}</p>}
        </div>
      )}
    </div>
  );
}
