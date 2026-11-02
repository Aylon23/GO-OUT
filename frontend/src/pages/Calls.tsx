import { Plus, Trash } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { Avatar } from "../components/Avatar";
import { SymbolSearch } from "../components/SymbolSearch";
import { EmptyState, ErrorLine, InfoTip, Segmented, Signed, Skel } from "../components/ui";
import { api, ApiError, type CallRow, type Calls as C, type SymbolItem } from "../lib/api";
import { dateLong, money, pct, pp, today } from "../lib/format";
import { invalidate, useData } from "../lib/hooks";

type Filter = "all" | "open" | "BUY" | "SELL";

export default function Calls() {
  const q = useData(api.calls, "calls");
  const d = q.data;
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const rows = (d?.rows ?? []).filter((r) => filter === "all" || (filter === "open" ? r.action === "BUY" && !r.closed_date : r.action === filter));
  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Recommendations</h1>
        <button type="button" className="btn-primary" onClick={() => setAdding((a) => !a)}><Plus size={16} /> Log a recommendation</button>
      </div>
      {adding && <CallForm onDone={() => { setAdding(false); invalidate(); }} onCancel={() => setAdding(false)} />}
      {q.error && !d ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <CallsSkeleton /> : d.rows.length === 0 ? (
        <EmptyState title="No recommendations yet."
          body="Every trade you add with Ctrl+K or from Telegram can be saved as a recommendation, and you can log one here. Each is then measured against the S&P 500 from its date." />
      ) : (
        <div className={clsx("transition-opacity duration", q.loading && "opacity-60")}>
          <Scorecard d={d} />
          <div className="mt-12 flex flex-wrap items-center justify-between gap-3">
            <Segmented label="Filter" size="sm" value={filter} onChange={setFilter}
              options={[{ value: "all", label: "All" }, { value: "open", label: "Still held" }, { value: "BUY", label: "Buys" }, { value: "SELL", label: "Sells" }]} />
            <p className="text-xs text-faint">Edge: the call's return minus the S&P 500 over the same days. For a sell: how much worse the stock did than the S&P after you sold.</p>
          </div>
          <CallTable rows={rows} base={d.base} />
        </div>
      )}
    </div>
  );
}

function Scorecard({ d }: { d: C }) {
  const s = d.scorecard.all;
  const b = d.scorecard.buy;
  const sl = d.scorecard.sell;
  return (
    <section className="mt-10" aria-label="Scorecard">
      <h2 className="max-w-[34ch] text-2xl font-medium md:text-3xl">
        {s.scored === 0 ? <>{s.count} recommendation{s.count === 1 ? "" : "s"}, too new to score.</> : (
          <><b className="font-semibold">{s.worked} of {s.scored}</b> recommendations beat the S&P 500
            {s.avg_edge != null && <span className="text-muted">, by <b className="font-semibold">{Math.abs(s.avg_edge * 100).toFixed(1)} points</b> {s.avg_edge >= 0 ? "on average" : "less on average"}</span>}.</>
        )}
      </h2>
      <dl className="mt-6 grid gap-x-10 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Buy calls" value={b.scored ? `${b.worked} of ${b.scored} worked` : `${b.count} logged`} sub={b.avg_edge != null ? `avg ${pp(b.avg_edge, 1)}` : undefined} />
        <Stat label="Sell calls" value={sl.scored ? `${sl.worked} of ${sl.scored} worked` : `${sl.count} logged`} sub={sl.avg_edge != null ? `avg ${pp(sl.avg_edge, 1)}` : undefined}
          tip="A sell worked when the stock then did worse than the S&P 500." />
        <Stat label="Best" value={s.best ? s.best.ticker : "–"} sub={s.best ? pp(s.best.edge, 1) : undefined} />
        <Stat label="Worst" value={s.worst ? s.worst.ticker : "–"} sub={s.worst ? pp(s.worst.edge, 1) : undefined} />
      </dl>
      {d.scorecard.avg_days_held != null && <p className="mt-3 text-xs text-faint">Closed buy calls were held {Math.round(d.scorecard.avg_days_held)} days on average.</p>}
    </section>
  );
}

function Stat({ label, value, sub, tip }: { label: string; value: string; sub?: string; tip?: string }) {
  return (
    <div className="border-t border-line pt-3">
      <dt className="text-xs text-muted">{tip ? <InfoTip text={tip}>{label}</InfoTip> : label}</dt>
      <dd className="mt-0.5 font-medium">{value}{sub && <span className="num ml-2 text-sm font-normal text-muted">{sub}</span>}</dd>
    </div>
  );
}

function CallTable({ rows, base }: { rows: CallRow[]; base: string }) {
  const [confirm, setConfirm] = useState<number | null>(null);
  if (!rows.length) return <p className="mt-6 text-sm text-muted">Nothing matches this filter.</p>;
  const cols = "grid-cols-[110px_minmax(150px,1fr)_minmax(120px,1fr)_96px_96px_96px_40px]";
  return (
    <div className="mt-4 text-sm" role="table" aria-label="Recommendations">
      <div role="row" className={clsx("sticky top-14 z-10 hidden gap-3 border-b border-line bg-bg py-2 text-xs font-medium text-muted lg:grid", cols)}>
        <span>Date</span><span>Call</span><span>Followed by</span>
        <span className="text-right">Return</span><span className="text-right">S&P 500</span><span className="text-right">Edge</span><span className="sr-only">Actions</span>
      </div>
      {rows.map((r) => (
        <div key={r.id} role="row" className="group border-b border-line py-3 lg:py-2.5">
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 lg:grid-cols-[110px_minmax(150px,1fr)_minmax(120px,1fr)_96px_96px_96px_40px] lg:items-center">
            <span className="num text-muted lg:text-text">{dateLong(r.date)}</span>
            <span className="col-span-2 row-start-1 flex min-w-0 items-center gap-2 lg:col-span-1 lg:row-start-auto">
              <Avatar ticker={r.ticker} size={22} />
              <span className={clsx("text-xs font-medium", r.action === "BUY" ? "text-text" : "text-muted")}>{r.action === "BUY" ? "Buy" : "Sell"}</span>
              <span className="font-mono font-semibold">{r.ticker}</span>
              {r.price != null && <span className="num text-xs text-faint">at {money(r.price, base, { decimals: 2 })}</span>}
              {r.action === "BUY" && (r.closed_date ? <span className="text-xs text-faint">sold {dateLong(r.closed_date)}</span> : <span className="text-xs text-faint">held</span>)}
            </span>
            <span className="truncate text-muted">{r.portfolios.join(", ") || "–"}</span>
            {r.error ? <span className="col-span-3 text-xs text-loss">{r.error}</span> : (
              <>
                <span className="text-right"><Signed value={r.return}>{pct(r.return, 1)}</Signed></span>
                <span className="num text-right text-muted">{pct(r.spy, 1)}</span>
                <span className="text-right font-medium"><Signed value={r.edge}>{pp(r.edge, 1)}</Signed></span>
              </>
            )}
            <span className="flex justify-end">
              {confirm === r.id ? (
                <button type="button" className="btn h-7 px-2 text-xs text-loss hover:bg-hover" autoFocus onBlur={() => setConfirm(null)}
                  onClick={async () => { await api.deleteCall(r.id); invalidate(); }}>Delete</button>
              ) : (
                <button type="button" aria-label={`Delete recommendation ${r.ticker}`} className="btn-ghost h-7 w-7 px-0 lg:opacity-0 lg:focus:opacity-100 lg:group-hover:opacity-100" onClick={() => setConfirm(r.id)}>
                  <Trash size={15} />
                </button>
              )}
            </span>
          </div>
          {(r.thesis || r.target != null) && (
            <p className="mt-1 max-w-[80ch] text-xs text-muted lg:pl-[122px]">
              {r.thesis}{r.target != null && <span className="text-faint">{r.thesis ? " · " : ""}target {money(r.target, base, { decimals: 2 })}{r.to_target != null && ` (${pct(r.to_target, 1)} to go)`}</span>}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function CallForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [sym, setSym] = useState<SymbolItem | null>(null);
  const [action, setAction] = useState<"BUY" | "SELL">("BUY");
  const [date, setDate] = useState(today());
  const [thesis, setThesis] = useState("");
  const [target, setTarget] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form className="fade-in mt-8 max-w-[760px] border-y border-line py-6" onSubmit={async (e) => {
      e.preventDefault();
      if (!sym) { setErr("Pick a stock."); return; }
      setBusy(true); setErr(null);
      try {
        await api.createCall({ ticker: sym.symbol, action, date, thesis, target: Number(target) > 0 ? Number(target) : null });
        onDone();
      } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't save."); } finally { setBusy(false); }
    }}>
      <p className="mb-5 text-sm text-muted">Log a call without trades, for example one you told someone about. Trades added with Ctrl+K or Telegram are logged automatically.</p>
      <Segmented label="Action" value={action} onChange={setAction} options={[{ value: "BUY", label: "Buy" }, { value: "SELL", label: "Sell" }]} />
      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <div>{sym ? (
          <div><span className="label">Stock</span><div className="flex h-9 items-center gap-2"><Avatar ticker={sym.symbol} size={22} /><span className="font-mono font-semibold">{sym.symbol}</span>
            <button type="button" className="link ml-auto text-sm" onClick={() => setSym(null)}>Change</button></div></div>
        ) : <SymbolSearch label="Stock" onSelect={setSym} />}</div>
        <div><label className="label" htmlFor="call-date">Date</label><input id="call-date" type="date" className="field" value={date} max={today()} onChange={(e) => setDate(e.target.value)} /></div>
        <div className="md:col-span-2"><label className="label" htmlFor="call-why">Why <span className="font-normal text-faint">(optional)</span></label>
          <input id="call-why" className="field" value={thesis} onChange={(e) => setThesis(e.target.value)} placeholder="For example: AI data-center demand, cheaper than peers" maxLength={300} /></div>
        {action === "BUY" && <div><label className="label" htmlFor="call-target">Target price <span className="font-normal text-faint">(optional)</span></label>
          <input id="call-target" inputMode="decimal" className="field num" value={target} onChange={(e) => setTarget(e.target.value)} /></div>}
      </div>
      {err && <p className="error-text" role="alert">{err}</p>}
      <div className="mt-6 flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving" : "Save recommendation"}</button>
      </div>
    </form>
  );
}

function CallsSkeleton() {
  return <div aria-hidden className="mt-10"><Skel className="h-10 w-[min(560px,100%)]" /><div className="mt-10 space-y-3">{[0, 1, 2, 3].map((i) => <Skel key={i} className="h-6 w-full" />)}</div></div>;
}
