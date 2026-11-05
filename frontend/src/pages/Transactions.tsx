import { DownloadSimple, FileXls, PencilSimple, Plus, Trash, UploadSimple } from "@phosphor-icons/react";
import clsx from "clsx";
import { useMemo, useRef, useState } from "react";
import { Avatar } from "../components/Avatar";
import { HoldingsImport } from "../components/HoldingsImport";
import { TxForm } from "../components/TxForm";
import { EmptyState, ErrorLine, Skel } from "../components/ui";
import { api, ApiError, type ImportResult, portfolioParam, type Tx, type TxType } from "../lib/api";
import { dateLong, money, num } from "../lib/format";
import { invalidate, useData } from "../lib/hooks";
import { useApp } from "../state";

const TYPE_LABEL: Record<TxType, string> = { BUY: "Buy", SELL: "Sell", DIVIDEND: "Dividend", DEPOSIT: "Deposit", WITHDRAWAL: "Withdrawal" };

export default function Transactions() {
  const { addTxFor, startAddTx, closeAddTx, settings } = useApp();
  const txs = useData(api.transactions, "tx");
  const [editing, setEditing] = useState<number | null>(null);
  const [fTicker, setFTicker] = useState("");
  const [fType, setFType] = useState<"" | TxType>("");
  const [fFrom, setFFrom] = useState("");
  const [fTo, setFTo] = useState("");
  const [confirmDel, setConfirmDel] = useState<number | null>(null);
  const [delErr, setDelErr] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(() => window.location.hash.includes("import=csv"));
  const [holdingsOpen, setHoldingsOpen] = useState(false);
  const { portfolio } = useApp();
  const base = settings?.base_currency ?? "USD";

  const rows = useMemo(() => (txs.data ?? []).filter((t) =>
    (!fTicker || t.ticker.includes(fTicker.trim().toUpperCase())) && (!fType || t.type === fType) && (!fFrom || t.date >= fFrom) && (!fTo || t.date <= fTo)), [txs.data, fTicker, fType, fFrom, fTo]);
  const filtered = !!(fTicker || fType || fFrom || fTo);

  async function remove(id: number) {
    setDelErr(null);
    try { await api.deleteTx(id); setConfirmDel(null); invalidate(); } catch (e) { setDelErr(e instanceof ApiError ? e.message : "Couldn't delete."); }
  }

  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Transactions{portfolio && <span className="text-muted"> · {portfolio.name}</span>}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-ghost" onClick={() => { setHoldingsOpen((o) => !o); setImportOpen(false); }}><FileXls size={16} /> Import holdings</button>
          <button type="button" className="btn-ghost" onClick={() => setImportOpen((o) => !o)}><UploadSimple size={16} /> Import CSV</button>
          <a className="btn-ghost" href={`/api/transactions/export?${portfolioParam()}`} download><DownloadSimple size={16} /> Export CSV</a>
          {addTxFor === undefined && <button type="button" className="btn-primary" onClick={() => startAddTx(null)}><Plus size={16} /> Add transaction</button>}
        </div>
      </div>

      {addTxFor !== undefined && (
        <section aria-label="Add transaction" className="mt-8 border-y border-line py-6">
          <h2 className="mb-5 text-lg font-semibold">New transaction</h2>
          <TxForm key={addTxFor?.symbol ?? "new"} symbol={addTxFor} baseCurrency={base}
            onCancel={closeAddTx} onDone={() => { closeAddTx(); invalidate(); }} />
        </section>
      )}

      {importOpen && <CsvImport onClose={() => setImportOpen(false)} />}
      {holdingsOpen && <HoldingsImport onClose={() => setHoldingsOpen(false)} />}

      <div className="mt-10 grid grid-cols-2 gap-3 md:flex md:items-end">
        <div className="col-span-2 md:w-44"><label className="label" htmlFor="f-t">Ticker</label><input id="f-t" className="field" placeholder="Any" value={fTicker} onChange={(e) => setFTicker(e.target.value)} /></div>
        <div className="col-span-2 md:w-40"><label className="label" htmlFor="f-ty">Type</label>
          <select id="f-ty" className="field" value={fType} onChange={(e) => setFType(e.target.value as TxType | "")}>
            <option value="">All types</option>{Object.entries(TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></div>
        <div className="md:w-40"><label className="label" htmlFor="f-f">From</label><input id="f-f" type="date" className="field px-2 text-sm" value={fFrom} onChange={(e) => setFFrom(e.target.value)} /></div>
        <div className="md:w-40"><label className="label" htmlFor="f-to">To</label><input id="f-to" type="date" className="field px-2 text-sm" value={fTo} onChange={(e) => setFTo(e.target.value)} /></div>
        {filtered && <button type="button" className="link col-span-2 h-9 text-sm md:ml-2" onClick={() => { setFTicker(""); setFType(""); setFFrom(""); setFTo(""); }}>Clear filters</button>}
      </div>

      {delErr && <ErrorLine message={delErr} />}
      <div className="mt-6">
        {txs.error ? <ErrorLine message={txs.error.message} onRetry={txs.reload} /> : !txs.data ? <ListSkeleton /> : txs.data.length === 0 ? (
          <EmptyState title="This portfolio is empty." body="Import what it holds today from Excel (ticker, quantity, average price), or add purchases one by one. Deposits are optional: buys without a deposit are treated as new money.">
            <button type="button" className="btn-primary" onClick={() => setHoldingsOpen(true)}><FileXls size={16} /> Import holdings</button>
            <button type="button" className="btn-secondary" onClick={() => startAddTx(null)}><Plus size={16} /> Add transaction</button>
            <button type="button" className="link text-sm" onClick={() => setImportOpen(true)}>Import CSV</button>
          </EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState title="Nothing matches these filters." body="Clear a filter to see more." />
        ) : (
          <div role="table" aria-label="Transactions" className="text-sm">
            <div role="row" className="sticky top-14 z-10 hidden grid-cols-[110px_96px_1fr_110px_120px_130px_88px] gap-3 border-b border-line bg-bg py-2 text-xs font-medium text-muted lg:grid">
              <span role="columnheader">Date</span><span role="columnheader">Type</span><span role="columnheader">Symbol</span>
              <span role="columnheader" className="text-right">Quantity</span><span role="columnheader" className="text-right">Price</span>
              <span role="columnheader" className="text-right">Amount</span><span role="columnheader" className="sr-only">Actions</span>
            </div>
            {rows.map((t) => editing === t.id ? (
              <div key={t.id} className="border-b border-line py-6">
                <TxForm initial={t} baseCurrency={base} onCancel={() => setEditing(null)} onDone={() => { setEditing(null); invalidate(); }} />
              </div>
            ) : (
              <TxRow key={t.id} t={t} confirming={confirmDel === t.id} onEdit={() => { setEditing(t.id!); setConfirmDel(null); }}
                onDelete={() => (confirmDel === t.id ? remove(t.id!) : setConfirmDel(t.id!))} onCancelDelete={() => setConfirmDel(null)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TxRow({ t, confirming, onEdit, onDelete, onCancelDelete }: { t: Tx; confirming: boolean; onEdit: () => void; onDelete: () => void; onCancelDelete: () => void }) {
  const trade = t.type === "BUY" || t.type === "SELL";
  const amount = trade ? t.quantity * t.price : t.price;
  const signed = t.type === "BUY" || t.type === "WITHDRAWAL" ? -amount : amount;
  const label = `${TYPE_LABEL[t.type]} on ${dateLong(t.date)}`;
  const symbol = t.ticker
    ? <span className="flex min-w-0 items-center gap-2"><Avatar ticker={t.ticker} size={22} /><span className="font-mono font-semibold">{t.ticker}</span></span>
    : <span className="text-faint">Cash</span>;
  const actions = confirming ? (
    <>
      <button type="button" className="btn-ghost h-7 px-2 text-xs" onClick={onCancelDelete}>Keep</button>
      <button type="button" className="btn h-7 px-2 text-xs text-loss hover:bg-hover" onClick={onDelete} autoFocus>Delete</button>
    </>
  ) : (
    <>
      <button type="button" aria-label={`Edit ${label}`} className="btn-ghost h-7 w-7 px-0 lg:opacity-0 lg:focus:opacity-100 lg:group-hover:opacity-100" onClick={onEdit}><PencilSimple size={15} /></button>
      <button type="button" aria-label={`Delete ${label}`} className="btn-ghost h-7 w-7 px-0 lg:opacity-0 lg:focus:opacity-100 lg:group-hover:opacity-100" onClick={onDelete}><Trash size={15} /></button>
    </>
  );
  return (
    <div role="row" className="group border-b border-line">
      {/* stacked rows below 1024px */}
      <div className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-0.5 py-3 lg:hidden">
        <span role="cell" className="flex min-w-0 items-center gap-2">{symbol}<span className="text-muted">{TYPE_LABEL[t.type]}</span></span>
        <span role="cell" className="num text-right">{money(signed, t.currency, { signed: true })}</span>
        <span className="num text-xs text-muted">{dateLong(t.date)}{trade && <> · {num(t.quantity, 4)} @ {money(t.price, t.currency, { decimals: 2 })}</>}</span>
        <span className="flex justify-end gap-1">{actions}</span>
      </div>
      {/* table row from 1024px */}
      <div className="hidden grid-cols-[110px_96px_1fr_110px_120px_130px_88px] items-center gap-3 py-2.5 lg:grid">
        <span role="cell" className="num">{dateLong(t.date)}</span>
        <span role="cell" className="text-muted">{TYPE_LABEL[t.type]}</span>
        <span role="cell" className="flex min-w-0 items-center gap-2">{symbol}{t.note && <span className="truncate text-xs text-faint">{t.note}</span>}</span>
        <span role="cell" className="num text-right">{trade ? num(t.quantity, 4) : ""}</span>
        <span role="cell" className="num text-right">{trade ? money(t.price, t.currency, { decimals: 2 }) : ""}</span>
        <span role="cell" className="num text-right">{money(signed, t.currency, { signed: true })}</span>
        <span role="cell" className="flex justify-end gap-1">{actions}</span>
      </div>
    </div>
  );
}

function ListSkeleton() {
  return <div aria-hidden>{Array.from({ length: 6 }).map((_, i) => <div key={i} className="flex items-center gap-4 border-b border-line py-3"><Skel className="h-4 w-24" /><Skel className="h-4 w-16" /><Skel className="h-4 w-20" /><div className="flex-1" /><Skel className="h-4 w-24" /></div>)}</div>;
}

function CsvImport({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<ImportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function run(f: File, commit: boolean) {
    setBusy(true); setErr(null);
    try {
      const r = await api.importCsv(f, commit);
      if (commit) { setDone(r.valid); setRes(null); invalidate(); } else setRes(r);
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't read the file."); }
    finally { setBusy(false); }
  }

  return (
    <section aria-label="Import CSV" className="fade-in mt-8 border-y border-line py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Import CSV</h2>
          <p className="mt-1 max-w-[62ch] text-sm text-muted">
            Columns: <span className="font-mono text-xs text-text">date,type,ticker,quantity,price,currency,fees,note</span>. Dates as YYYY-MM-DD. For dividends, deposits and withdrawals, put the cash amount in <span className="font-mono text-xs text-text">price</span>.
          </p>
        </div>
        <button type="button" className="btn-ghost" onClick={onClose}>Close</button>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <input ref={input} type="file" accept=".csv,text/csv" className="sr-only" id="csv-file"
          onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); setRes(null); setDone(null); if (f) run(f, false); }} />
        <label htmlFor="csv-file" className="btn-secondary cursor-pointer">Choose file</label>
        <span className="text-sm text-muted">{file ? file.name : "No file chosen"}</span>
      </div>
      {err && <ErrorLine message={err} />}
      {done !== null && <p className="mt-4 text-sm">Imported {done} transaction{done === 1 ? "" : "s"}.</p>}
      {busy && !res && <div className="mt-4 space-y-2"><Skel className="h-4 w-full" /><Skel className="h-4 w-full" /><Skel className="h-4 w-2/3" /></div>}
      {res && (
        <div className="mt-5">
          <p className="text-sm"><span className="font-medium">{res.valid}</span> ready to import{res.invalid > 0 && <>, <span className="font-medium text-loss">{res.invalid}</span> with errors (skipped)</>}.</p>
          <div className="mt-3 max-h-[360px] overflow-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="sticky top-0 bg-bg text-xs text-muted"><tr className="border-b border-line">
                <th className="py-2 pr-3 font-medium">Line</th><th className="py-2 pr-3 font-medium">Date</th><th className="py-2 pr-3 font-medium">Type</th><th className="py-2 pr-3 font-medium">Ticker</th>
                <th className="py-2 pr-3 text-right font-medium">Qty</th><th className="py-2 pr-3 text-right font-medium">Price</th><th className="py-2 font-medium">Status</th></tr></thead>
              <tbody>
                {res.rows.map((r) => {
                  const bad = Object.keys(r.errors).length > 0;
                  return (
                    <tr key={r.line} className="border-b border-line align-top">
                      <td className="num py-2 pr-3 text-faint">{r.line}</td>
                      <td className="num py-2 pr-3">{r.raw.date}</td><td className="py-2 pr-3">{r.raw.type}</td>
                      <td className="py-2 pr-3 font-mono">{r.raw.ticker}</td>
                      <td className="num py-2 pr-3 text-right">{r.raw.quantity}</td><td className="num py-2 pr-3 text-right">{r.raw.price}</td>
                      <td className={clsx("py-2", bad ? "text-loss" : "text-muted")}>{bad ? Object.entries(r.errors).map(([k, v]) => `${k}: ${v}`).join(" ") : "OK"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex gap-2">
            <button type="button" className="btn-primary" disabled={!res.valid || busy} onClick={() => file && run(file, true)}>
              Import {res.valid} row{res.valid === 1 ? "" : "s"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => { setRes(null); setFile(null); if (input.current) input.current.value = ""; }}>Start over</button>
          </div>
        </div>
      )}
    </section>
  );
}
