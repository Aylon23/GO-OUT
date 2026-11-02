import { X } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { api, ApiError, type Ccy, type NewMoney as NM, type NewMoneyRow } from "../lib/api";
import { money, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { useApp } from "../state";
import { Avatar } from "./Avatar";
import { SymbolSearch } from "./SymbolSearch";
import { ErrorLine, InfoTip, Segmented, Signed, Skel } from "./ui";

const ils = (v: number, signed = true) => money(v, "ILS", { decimals: 0, signed });

function cell(v: number | undefined) {
  if (v == null) return "transparent";
  const a = Math.min(Math.abs(v) / 0.05, 1);
  return v >= 0 ? `color-mix(in oklch, var(--gain) ${Math.round(a * 45)}%, transparent)` : `color-mix(in oklch, var(--loss) ${Math.round(a * 45)}%, transparent)`;
}

export function NewMoney() {
  const { portfolios } = useApp();
  const [amount, setAmount] = useState("20000");
  const [ccy, setCcy] = useState<Ccy>("ILS");
  const [person, setPerson] = useState<string>("");
  const [d, setD] = useState<NM | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sort, setSort] = useState<"robust" | "expected">("robust");
  const book = useData(api.book, "book");
  const hasBook = !!book.data?.entries.some((e) => e.probability > 0);
  const [useBook, setUseBook] = useState(true);
  const run = async () => {
    const a = Number(amount.replace(/,/g, ""));
    if (!isFinite(a) || a <= 0) { setErr("Enter an amount above zero."); return; }
    setBusy(true); setErr(null);
    try { setD(await api.newMoney({ amount: a, currency: ccy, person: person ? Number(person) : null, use_book: hasBook && useBook })); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't rank them."); } finally { setBusy(false); }
  };
  const rows = d ? (sort === "robust" ? d.rows : [...d.rows].sort((a, b) => b.expected - a.expected)).slice(0, 10) : [];
  return (
    <section className="mt-8">
      <p className="max-w-[70ch] text-sm text-muted">Where new money holds up best across your saved scenarios and normal conditions. Sorted by how robust each choice is, not by its best case.</p>
      <form className="mt-5 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <div><label className="label" htmlFor="nm-amount">Amount</label>
          <input id="nm-amount" inputMode="decimal" className="field num w-36" value={amount} onChange={(e) => setAmount(e.target.value)} /></div>
        <Segmented label="Currency" value={ccy} onChange={setCcy} options={[{ value: "ILS", label: "₪" }, { value: "USD", label: "$" }]} />
        <div><label className="label" htmlFor="nm-person">For</label>
          <select id="nm-person" className="field w-44" value={person} onChange={(e) => setPerson(e.target.value)}>
            <option value="">Anyone</option>{portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select></div>
        {hasBook && <label className="flex h-9 items-center gap-2 text-sm text-muted"><input type="checkbox" className="accent-[var(--accent)]" checked={useBook} onChange={(e) => setUseBook(e.target.checked)} />Weight by my book</label>}
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Ranking" : "Rank"}</button>
      </form>
      {err && <ErrorLine message={err} />}
      {busy && !d && <div className="mt-6 space-y-2"><Skel className="h-6 w-1/2" /><Skel className="h-48 w-full" /></div>}
      {d && (
        <div className={clsx("mt-8", busy && "opacity-60")}>
          {d.note && <p className="mb-3 text-sm text-muted">{d.note}</p>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Top 10 for {money(d.amount, d.currency, { decimals: 0 })}{person && ` for ${portfolios.find((p) => String(p.id) === person)?.name}`}</h2>
            <Segmented label="Sort" size="sm" value={sort} onChange={setSort} options={[{ value: "robust", label: "Most robust" }, { value: "expected", label: "Best expected" }]} />
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[640px] border-separate border-spacing-0 text-sm">
              <thead><tr className="text-xs text-muted">
                <th className="border-b border-line py-2 text-left font-medium">#</th>
                <th className="border-b border-line py-2 text-left font-medium">Candidate</th>
                <th className="border-b border-line py-2 pl-3 text-right font-medium"><span className="inline-flex items-center gap-1">Holds up<InfoTip text="Branches (and normal conditions) where its middle outcome is positive or near zero." /></span></th>
                <th className="border-b border-line py-2 pl-3 text-right font-medium">Expected</th>
                <th className="border-b border-line py-2 pl-3 text-right font-medium">Bad case</th>
                {d.scenarios.map((s) => <th key={s.id} className="max-w-[120px] border-b border-line py-2 pl-3 text-right font-medium" title={s.title}><span className="line-clamp-2">{s.title}</span></th>)}
              </tr></thead>
              <tbody>{rows.map((r: NewMoneyRow, i) => (
                <tr key={r.symbol}>
                  <td className="num border-b border-line py-2 text-faint">{i + 1}</td>
                  <td className="border-b border-line py-2"><span className="flex items-center gap-2"><Avatar ticker={r.symbol} size={20} /><span className="font-mono font-semibold">{r.symbol}</span>
                    {r.robust && <span className="rounded-sm bg-accent-soft px-1.5 text-[11px] font-medium">robust</span>}
                    {r.low_confidence && <span className="text-[11px] text-faint">low confidence</span>}</span></td>
                  <td className="num border-b border-line py-2 pl-3 text-right">{r.robust_count}/{r.branches}</td>
                  <td className="num border-b border-line py-2 pl-3 text-right"><Signed value={r.expected_ils}>{ils(r.expected_ils)}</Signed></td>
                  <td className="num border-b border-line py-2 pl-3 text-right"><Signed value={r.bad_ils}>{ils(r.bad_ils)}</Signed></td>
                  {d.scenarios.map((s) => (
                    <td key={s.id} className="num border-b border-line py-2 pl-3 text-right text-xs" style={{ background: cell(r.by_scenario[s.id]) }}>{pct(r.by_scenario[s.id], 1)}</td>
                  ))}
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="mt-2 max-w-[80ch] text-xs text-faint">{d.method} Ideas, not advice.</p>
        </div>
      )}
      <Watchlist onChange={() => setD(null)} />
    </section>
  );
}

function Watchlist({ onChange }: { onChange: () => void }) {
  const q = useData(api.watchlist, "watchlist");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="mt-12">
      <h3 className="text-base font-semibold">Watchlist</h3>
      <p className="mt-1 text-sm text-muted">Candidates besides what the family holds, the sector funds, SPY, QQQ, TLT and GLD. Add from search, a stock's page, or type “watch AVGO” in Ctrl+K.</p>
      <ul className="mt-3 flex flex-wrap gap-2">
        {(q.data ?? []).map((s) => (
          <li key={s} className="flex h-8 items-center gap-2 rounded-sm border border-line bg-surface pl-1.5 pr-1 text-sm">
            <Avatar ticker={s} size={20} /><span className="font-mono font-semibold">{s}</span>
            <button type="button" aria-label={`Stop watching ${s}`} className="btn-ghost h-6 w-6 px-0" onClick={async () => { await api.unwatch(s); q.reload(); onChange(); }}><X size={12} /></button>
          </li>
        ))}
      </ul>
      <div className="mt-3 max-w-[360px]"><SymbolSearch placeholder="Add to watchlist" exclude={q.data ?? []}
        onSelect={async (s) => { setErr(null); try { await api.watch(s.symbol); q.reload(); onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't add it."); } }} /></div>
      {err && <p className="error-text">{err}</p>}
    </div>
  );
}
