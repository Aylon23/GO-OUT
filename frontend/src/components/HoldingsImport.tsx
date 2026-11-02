import clsx from "clsx";
import { useRef, useState } from "react";
import { api, ApiError, type HoldingImport } from "../lib/api";
import { money, num, pct, today } from "../lib/format";
import { invalidate } from "../lib/hooks";
import { useApp } from "../state";
import { ErrorLine, Signed, Skel } from "./ui";

/** Import a list of current holdings (ticker, quantity, average price) from Excel or CSV. */
export function HoldingsImport({ onClose }: { onClose: () => void }) {
  const { portfolio, reloadPortfolios } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [asOf, setAsOf] = useState(today());
  const [res, setRes] = useState<HoldingImport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function run(f: File, commit: boolean) {
    setBusy(true); setErr(null);
    try {
      const r = await api.importHoldings(f, commit, asOf);
      if (commit) { setDone(r.imported); setRes(null); invalidate(); reloadPortfolios().catch(() => {}); } else setRes(r);
    } catch (e) { setRes(null); setErr(e instanceof ApiError ? e.message : "Couldn't read the file."); }
    finally { setBusy(false); }
  }

  return (
    <section aria-label="Import holdings" className="fade-in mt-8 border-y border-line py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Import holdings into {portfolio?.name ?? "this portfolio"}</h2>
          <p className="mt-1 max-w-[66ch] text-sm text-muted">
            An Excel or CSV file with a header row. Columns: <span className="font-mono text-xs text-text">ticker, quantity, avg_price, currency</span>{" "}
            (Hebrew headers work too: סימול, כמות, שער ממוצע, מטבע). Tel Aviv tickers end in <span className="font-mono text-xs text-text">.TA</span>; prices in agorot are converted.
          </p>
        </div>
        <button type="button" className="btn-ghost" onClick={onClose}>Close</button>
      </div>
      <div className="mt-5 flex flex-wrap items-end gap-4">
        <div>
          <span className="label">File</span>
          <div className="flex items-center gap-3">
            <input ref={input} id="hold-file" type="file" accept=".xlsx,.xlsm,.csv" className="sr-only"
              onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); setRes(null); setDone(null); if (f) run(f, false); }} />
            <label htmlFor="hold-file" className="btn-secondary cursor-pointer">Choose file</label>
            <span className="max-w-[220px] truncate text-sm text-muted">{file ? file.name : "No file chosen"}</span>
          </div>
        </div>
        <div>
          <label className="label" htmlFor="hold-asof">Holdings as of</label>
          <input id="hold-asof" type="date" className="field w-44" value={asOf} max={today()}
            onChange={(e) => { setAsOf(e.target.value); if (file) run(file, false); }} />
        </div>
      </div>
      <p className="helper max-w-[66ch]">Real performance is tracked from this date. Gain and loss use your average price.</p>
      {err && <ErrorLine message={err} />}
      {done !== null && <p className="mt-4 text-sm">Imported {done} holding{done === 1 ? "" : "s"}.</p>}
      {busy && !res && <div className="mt-4 space-y-2"><Skel className="h-4 w-full" /><Skel className="h-4 w-full" /><Skel className="h-4 w-2/3" /></div>}
      {res && (
        <div className="mt-5">
          <p className="text-sm"><span className="font-medium">{res.valid}</span> ready{res.invalid > 0 && <>, <span className="font-medium text-loss">{res.invalid}</span> with errors (skipped)</>}.</p>
          <div className="mt-3 max-h-[380px] overflow-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="sticky top-0 bg-bg text-xs text-muted"><tr className="border-b border-line">
                <th className="py-2 pr-3 font-medium">Line</th><th className="py-2 pr-3 font-medium">Ticker</th>
                <th className="py-2 pr-3 text-right font-medium">Quantity</th><th className="py-2 pr-3 text-right font-medium">Avg price</th>
                <th className="py-2 pr-3 text-right font-medium">Price now</th><th className="py-2 pr-3 text-right font-medium">Gain</th><th className="py-2 font-medium">Status</th>
              </tr></thead>
              <tbody>
                {res.rows.map((r) => {
                  const bad = Object.keys(r.errors).length > 0;
                  return (
                    <tr key={r.line} className="border-b border-line align-top">
                      <td className="num py-2 pr-3 text-faint">{r.line}</td>
                      <td className="py-2 pr-3 font-mono font-semibold">{r.ticker || "–"}</td>
                      <td className="num py-2 pr-3 text-right">{r.quantity !== undefined ? num(r.quantity, 4) : ""}</td>
                      <td className="num py-2 pr-3 text-right">{r.avg_price !== undefined ? money(r.avg_price, r.currency ?? "USD", { decimals: 2 }) : ""}</td>
                      <td className="num py-2 pr-3 text-right">{r.price !== undefined ? money(r.price, r.currency ?? "USD", { decimals: 2 }) : ""}</td>
                      <td className="py-2 pr-3 text-right">{r.gain_pct !== undefined && <Signed value={r.gain_pct}>{pct(r.gain_pct, 1)}</Signed>}</td>
                      <td className={clsx("py-2", bad ? "text-loss" : "text-muted")}>
                        {bad ? Object.values(r.errors).join(" ") : r.notes.length ? r.notes.join(" ") : "OK"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex gap-2">
            <button type="button" className="btn-primary" disabled={!res.valid || busy} onClick={() => file && run(file, true)}>
              Import {res.valid} holding{res.valid === 1 ? "" : "s"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => { setRes(null); setFile(null); if (input.current) input.current.value = ""; }}>Start over</button>
          </div>
        </div>
      )}
    </section>
  );
}
