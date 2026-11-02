import { Tray } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { api, ApiError, type EarningsCard } from "../lib/api";
import { amt, dateLong, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { Avatar } from "./Avatar";
import { EmptyState, ErrorLine, InfoTip, Signed, Skel } from "./ui";

const fmt = (v: number | null | undefined, signed = true) => amt(v, signed);

export function Earnings() {
  const [date, setDate] = useState<string | undefined>(undefined);
  const q = useData(() => api.earnings(date), `earnings-${date ?? "latest"}`);
  const inbox = useData(api.inbox, "inbox");
  const d = q.data;
  if (q.error && !d) return <ErrorLine message={q.error.message} onRetry={q.reload} />;
  if (!d) return <div className="mt-8 space-y-3"><Skel className="h-40 w-full" /><Skel className="h-40 w-full" /></div>;
  const missingToday = !d.date || (d.date < d.last_trading_day && !date);
  return (
    <section className="mt-8">
      {missingToday && (
        <EmptyState title="No brief today. The morning task runs at 08:30 when your computer is on."
          body={d.date ? `Showing the latest brief, from ${dateLong(d.date)}.` : "When a brief arrives in the inbox folder, it shows up here."} />
      )}
      {d.dates.length > 1 && (
        <label className="flex items-center gap-2 text-sm text-muted">Day
          <select className="field h-8 w-auto py-0 text-sm" value={d.date ?? ""} onChange={(e) => setDate(e.target.value)}>
            {d.dates.map((x) => <option key={x} value={x}>{dateLong(x)}</option>)}
          </select>
        </label>
      )}
      {d.date && !missingToday && <h2 className="text-lg font-semibold">Reported {dateLong(d.date)}</h2>}
      <ul className="mt-6 space-y-6">{d.companies.map((c) => <Card key={c.symbol} c={c} />)}</ul>
      <Journal />
      <p className="mt-10 flex flex-wrap items-center gap-2 text-xs text-muted"><Tray size={14} />The scheduled task drops briefs into <span className="select-all font-mono text-text">{inbox.data?.path ?? "…"}</span>
        (format: docs/earnings-brief.schema.json). <PasteBrief onDone={q.reload} /></p>
    </section>
  );
}

function Chip({ children, tone }: { children: React.ReactNode; tone: "gain" | "loss" | "muted" }) {
  return <span className={clsx("inline-flex h-6 items-center rounded-sm px-2 text-xs font-medium",
    tone === "gain" ? "bg-[color-mix(in_oklch,var(--gain)_18%,transparent)] text-gain" : tone === "loss" ? "bg-[color-mix(in_oklch,var(--loss)_18%,transparent)] text-loss" : "bg-sunken text-muted")}>{children}</span>;
}

function Card({ c }: { c: EarningsCard }) {
  const r = c.data.results;
  const g = c.data.guidance;
  const s = c.history.summary;
  const o = c.data.outlook;
  return (
    <li className="rounded-md border border-line p-4 md:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Avatar ticker={c.symbol} size={32} />
          <div><p className="font-semibold">{c.name}</p><p className="font-mono text-xs text-faint">{c.symbol} · {c.data.reported === "after_close" ? "after the close" : "before the open"}</p></div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {c.beat.revenue && <Chip tone={c.beat.revenue === "beat" ? "gain" : c.beat.revenue === "miss" ? "loss" : "muted"}>Revenue {c.beat.revenue}</Chip>}
          {c.beat.eps && <Chip tone={c.beat.eps === "beat" ? "gain" : c.beat.eps === "miss" ? "loss" : "muted"}>EPS {c.beat.eps}</Chip>}
          {g !== "none" && <Chip tone={g === "raised" ? "gain" : g === "lowered" ? "loss" : "muted"}>Guidance {g}</Chip>}
          {c.journal.score && <Chip tone={c.journal.score === "hit" ? "gain" : c.journal.score === "miss" ? "loss" : "muted"}>Outlook: {c.journal.score}</Chip>}
        </div>
      </div>
      {(r.revenue != null || r.eps != null) && (
        <p className="num mt-3 text-xs text-muted">
          {r.revenue != null && <>Revenue {r.revenue} vs {r.revenue_est ?? "–"} expected</>}{r.revenue != null && r.eps != null && " · "}
          {r.eps != null && <>EPS {r.eps} vs {r.eps_est ?? "–"}</>}{r.currency && ` (${r.currency})`}
        </p>
      )}
      <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <p className="text-sm">
            {c.today ? <>Reaction on {dateLong(c.today.reaction_date)}: <Signed value={c.today.reaction} className="font-semibold">{pct(c.today.reaction, 1)}</Signed></> : <span className="text-muted">No reaction-day price yet.</span>}
            {c.bigger_than_share != null && <span className="text-muted"> · bigger than {pct(c.bigger_than_share, 0, false)} of the last {s?.n} reports</span>}
          </p>
          <ReactionChart c={c} />
          {s && <p className="mt-1 text-xs text-muted">Last {s.n} reports: typical first-day move ±{pct(s.reaction_abs_avg, 1, false)}; after 20 sessions {pct(s.d20_min, 0)} to {pct(s.d20_max, 0)}.
            {s.continued_share != null && <> The first-day direction held for 20 sessions {pct(s.continued_share, 0, false)} of the time.</>}</p>}
          <div className="mt-4">
            <p className="text-sm font-medium">Family</p>
            {c.impact.holders.length === 0 ? <p className="text-sm text-muted">Nobody holds it.</p> : (
              <ul className="mt-1 text-sm">{c.impact.holders.map((h) => (
                <li key={h.id} className="flex justify-between gap-3 border-b border-line py-1"><span>{h.name} <span className="text-xs text-faint">{pct(h.weight, 1, false)} of the portfolio</span></span>
                  <Signed value={h.pnl_amt}>{h.pnl_amt == null ? "–" : fmt(h.pnl_amt)}</Signed></li>))}</ul>
            )}
            {c.impact.related.length > 0 && (
              <>
                <p className="mt-3 flex items-center gap-1 text-xs font-medium text-muted">Related holdings, expected move<InfoTip text="Each holding's weekly-return beta to this company over two years, times today's reaction." /></p>
                <ul className="mt-1 text-xs">{c.impact.related.slice(0, 5).map((x) => (
                  <li key={x.symbol} className="flex justify-between gap-3 py-0.5"><span><span className="font-mono font-semibold">{x.symbol}</span> <span className="text-faint">{x.why}, held by {x.holders.join(", ")}</span></span>
                    <span><Signed value={x.expected_move}>{pct(x.expected_move, 1)}</Signed> <Signed value={x.expected_amt} className="text-faint">{fmt(x.expected_amt)}</Signed></span></li>))}</ul>
              </>
            )}
          </div>
        </div>
        <div className="text-sm">
          {c.data.key_points.length > 0 && <ul className="list-disc space-y-0.5 pl-5 text-muted">{c.data.key_points.map((k) => <li key={k}>{k}</li>)}</ul>}
          {c.data.guidance_detail && <p className="mt-2 text-muted">{c.data.guidance_detail}</p>}
          <p className="mt-3 font-medium">Outlook <span className="text-xs font-normal text-faint">{o.confidence} confidence</span></p>
          <p className="mt-1"><span className="text-faint">Next days: </span>{o.days}</p>
          <p className="mt-1"><span className="text-faint">Next weeks: </span>{o.weeks}</p>
          {o.bull_case && <p className="mt-1 text-muted"><span className="text-gain">Bull:</span> {o.bull_case}</p>}
          {o.bear_case && <p className="mt-1 text-muted"><span className="text-loss">Bear:</span> {o.bear_case}</p>}
          {c.data.sources?.length ? <p className="mt-3 text-xs">{c.data.sources.map((x, i) => <span key={x.url}>{i > 0 && " · "}<a className="link" href={x.url} target="_blank" rel="noreferrer noopener">{x.title}</a></span>)}</p> : null}
        </div>
      </div>
    </li>
  );
}

function ReactionChart({ c }: { c: EarningsCard }) {
  const ev = c.history.events;
  const vals = [...ev.map((e) => e.reaction), ...(c.today ? [c.today.reaction] : [])];
  if (!vals.length) return null;
  const max = Math.max(...vals.map(Math.abs), 0.01);
  const w = 260, h = 64, bw = w / Math.max(vals.length, 1);
  return (
    <svg viewBox={`0 0 ${w} ${h + 14}`} className="mt-2 w-full max-w-[300px]" role="img" aria-label="First-day moves after the last reports, today last">
      <line x1={0} x2={w} y1={h / 2} y2={h / 2} className="stroke-line" />
      {vals.map((v, i) => {
        const today = c.today && i === vals.length - 1;
        const bh = (Math.abs(v) / max) * (h / 2 - 2);
        return <rect key={i} x={i * bw + 2} width={bw - 4} y={v >= 0 ? h / 2 - bh : h / 2} height={Math.max(bh, 1)}
          className={clsx(v >= 0 ? "fill-gain" : "fill-loss")} opacity={today ? 1 : 0.4} />;
      })}
      {c.today && <text x={(vals.length - 0.5) * bw} y={h + 12} textAnchor="middle" className="fill-text text-[10px]">today</text>}
      {ev.length > 0 && <text x={2} y={h + 12} className="fill-muted text-[10px]">{ev[0].date.slice(0, 7)}</text>}
    </svg>
  );
}

function Journal() {
  const q = useData(api.earningsJournal, "earnings-journal");
  const d = q.data;
  if (!d || d.rows.length === 0) return null;
  const rate = (x: { n: number; hit: number | null }) => (x.n ? `${pct(x.hit, 0, false)} of ${x.n}` : "–");
  return (
    <div className="mt-12">
      <h3 className="text-base font-semibold">Prediction journal</h3>
      <p className="mt-1 text-xs text-muted">Each outlook is checked 5 and 20 sessions after the reaction day. Hit = moved the predicted way (flat = within 1% at 5 days, 2% at 20).</p>
      <table className="mt-3 w-full max-w-[560px] text-sm">
        <thead><tr className="text-xs text-muted"><th className="py-1 text-left font-medium">Confidence</th><th className="py-1 text-right font-medium">5 sessions</th><th className="py-1 text-right font-medium">20 sessions</th><th className="py-1 text-right font-medium">Overall</th></tr></thead>
        <tbody>{(["high", "medium", "low"] as const).map((k) => (
          <tr key={k} className="border-t border-line"><td className="py-1.5">{k}</td>
            <td className="num py-1.5 text-right">{rate(d.accuracy[k].d5)}</td><td className="num py-1.5 text-right">{rate(d.accuracy[k].d20)}</td><td className="num py-1.5 text-right">{rate(d.accuracy[k].overall)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function PasteBrief({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  if (!open) return <button type="button" className="link text-xs" onClick={() => setOpen(true)}>Or paste one</button>;
  return (
    <span className="mt-2 block w-full">
      <textarea aria-label="Paste an earnings brief" className="field h-28 py-2 font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} />
      <span className="mt-2 flex items-center gap-2">
        <button type="button" className="btn-secondary h-8" disabled={!text.trim()} onClick={async () => {
          try { const r = await api.earningsImport(text); setMsg(`Imported ${r.companies} companies for ${r.date}.`); setText(""); onDone(); } catch (e) { setMsg(e instanceof ApiError ? e.message : "Couldn't import it."); }
        }}>Import</button>
        <button type="button" className="btn-ghost h-8" onClick={() => setOpen(false)}>Close</button>
        {msg && <span className="whitespace-pre-wrap">{msg}</span>}
      </span>
    </span>
  );
}
