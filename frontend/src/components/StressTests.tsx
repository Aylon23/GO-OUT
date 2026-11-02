import clsx from "clsx";
import { useState } from "react";
import { api, ApiError, type ReverseStress, type RiskBook, type StressTest } from "../lib/api";
import { amt, dateLong, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { ErrorLine, InfoTip, Signed, Skel } from "./ui";

const fmt = (v: number | null | undefined, signed = true) => amt(v, signed);

/** Under Risk: the family risk summary, historical stress tests and the reverse stress test. */
export function StressSection() {
  return (
    <>
      <RiskSummary />
      <StressTests />
    </>
  );
}

function RiskSummary() {
  const q = useData(api.riskSummary, "risk-summary");
  const [sel, setSel] = useState(0);
  const d = q.data;
  if (q.error && !d) return <section className="mt-16"><ErrorLine message={q.error.message} onRetry={q.reload} /></section>;
  if (!d) return <section className="mt-16" aria-hidden><Skel className="h-6 w-40" /><Skel className="mt-3 h-24 w-full" /></section>;
  if (d.empty) return null;
  const b: RiskBook = d.books[Math.min(sel, d.books.length - 1)];
  return (
    <section className="mt-16" aria-label="Risk summary">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-lg font-semibold">Risk summary</h2>
        <select aria-label="Portfolio" className="field h-8 w-auto py-0 text-sm" value={sel} onChange={(e) => setSel(Number(e.target.value))}>
          {d.books.map((x, i) => <option key={x.id ?? "family"} value={i}>{x.name}</option>)}
        </select>
      </div>
      <p className="mt-1 max-w-[70ch] text-sm text-muted">Today's holdings over the last {Math.round(b.weeks / 52 * 10) / 10} years of weekly returns. Stock performance only, currency effects excluded.</p>
      <dl className="mt-4 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
        <Stat k="Bad week (1 in 20)" tip="Historical 95% value at risk: in 1 week out of 20 the loss was at least this." v={fmt(-b.var_1w_amt)} sub={pct(-b.var_1w, 1)} />
        <Stat k="Beyond it, on average" tip="Average loss in those worst weeks (expected shortfall, CVaR)." v={fmt(-b.cvar_1w_amt)} sub={pct(-b.cvar_1w, 1)} />
        <Stat k="Bad month (1 in 20)" tip="The same for 4-week stretches." v={b.var_1m_amt != null ? fmt(-b.var_1m_amt) : "–"} sub={b.var_1m != null ? pct(-b.var_1m, 1) : ""} />
        <Stat k="Independent bets" tip="How many truly separate bets the portfolio's risk is spread over (effective number of bets). Far below the number of holdings means they move together." v={b.effective_bets != null ? b.effective_bets.toFixed(1) : "–"} sub={`of ${b.holdings} holdings`} />
      </dl>
      <div className="mt-6 max-w-[560px]">
        <p className="text-sm font-medium">Where the risk comes from</p>
        <ul className="mt-2 space-y-1.5">{b.risk.slice(0, 6).map((r) => (
          <li key={r.symbol} className="grid grid-cols-[72px_1fr_auto] items-center gap-3 text-sm">
            <span className="font-mono font-semibold">{r.symbol}</span>
            <span className="relative h-2 rounded-full bg-sunken" aria-hidden>
              <span className="absolute inset-y-0 left-0 rounded-full bg-line-strong" style={{ width: `${Math.min(100, r.value_share * 100)}%` }} />
              <span className="absolute inset-y-0 left-0 rounded-full bg-accent opacity-80" style={{ width: `${Math.min(100, Math.max(0, r.risk_share) * 100)}%`, height: "50%", top: "25%" }} />
            </span>
            <span className="num text-xs text-muted">{pct(r.risk_share, 0, false)} of risk · {pct(r.value_share, 0, false)} of value</span>
          </li>
        ))}</ul>
      </div>
    </section>
  );
}

function Stat({ k, v, sub, tip }: { k: string; v: string; sub: string; tip: string }) {
  return (
    <div><dt className="flex items-center gap-1 text-xs text-muted">{k}<InfoTip text={tip} /></dt>
      <dd className="num mt-1 text-xl font-semibold text-loss">{v}</dd><dd className="num text-xs text-faint">{sub}</dd></div>
  );
}

function StressTests() {
  const [tests, setTests] = useState<StressTest[] | null>(null);
  const [rev, setRev] = useState<ReverseStress | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const s = await api.stress();
      setTests(s.tests ?? []);
      setRev(await api.reverseStress());
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't run them."); } finally { setBusy(false); }
  };
  const ports = tests?.[0]?.portfolios ?? [];
  return (
    <section className="mt-16" aria-label="Stress tests">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Stress tests</h2>
          <p className="mt-1 max-w-[70ch] text-sm text-muted">Today's holdings through past crises, using real prices in each stock's own currency (currency effects excluded). A holding with no prices then is estimated from its factor exposures.</p>
        </div>
        <button type="button" className="btn-primary" disabled={busy} onClick={run}>{busy ? "Running" : tests ? "Run again" : "Run all stress tests"}</button>
      </div>
      {err && <ErrorLine message={err} />}
      {tests && (
        <div className="mt-5 overflow-x-auto">
          <table className="w-full min-w-[720px] border-separate border-spacing-0 text-sm">
            <thead><tr className="text-xs text-muted">
              <th className="border-b border-line py-2 text-left font-medium">Period</th>
              <th className="border-b border-line py-2 pl-3 text-right font-medium">S&P 500</th>
              <th className="border-b border-line py-2 pl-3 text-right font-medium">Family</th>
              <th className="border-b border-line py-2 pl-3 text-right font-medium">Amount</th>
              {ports.map((p) => <th key={p.id} className="border-b border-line py-2 pl-3 text-right font-medium">{p.name}</th>)}
            </tr></thead>
            <tbody>{tests.map((t) => (
              <tr key={t.id}>
                <td className="border-b border-line py-2.5"><span className="font-medium">{t.name}</span>
                  <span className="block text-xs text-faint">{t.data ? `${dateLong(t.used_start!)} – ${dateLong(t.used_end!)}` : `${dateLong(t.start)} – ${dateLong(t.end)} · no price data`}
                    {t.family.estimated_share != null && t.family.estimated_share > 0.01 && ` · ${pct(t.family.estimated_share, 0, false)} estimated`}</span></td>
                <td className="num border-b border-line py-2.5 pl-3 text-right">{t.sp500_usd != null ? <Signed value={t.sp500_usd}>{pct(t.sp500_usd, 1)}</Signed> : "–"}</td>
                <td className="num border-b border-line py-2.5 pl-3 text-right font-semibold">{t.family.impact != null ? <Signed value={t.family.impact}>{pct(t.family.impact, 1)}</Signed> : "–"}</td>
                <td className="num border-b border-line py-2.5 pl-3 text-right">{t.family.change_amt != null ? <Signed value={t.family.change_amt}>{fmt(t.family.change_amt)}</Signed> : "–"}</td>
                {t.portfolios.map((p) => <td key={p.id} className="num border-b border-line py-2.5 pl-3 text-right">{p.impact != null ? <Signed value={p.impact}>{pct(p.impact, 1)}</Signed> : "–"}</td>)}
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {rev && !rev.empty && (
        <div className="mt-10">
          <h3 className="text-base font-semibold">What would cost the family 10% or 20%?</h3>
          <ul className="mt-3 space-y-4">{rev.results.map((r) => (
            <li key={r.loss} className="max-w-[80ch] border-t border-line pt-3 text-sm">
              <p>{r.sentence}</p>
              {r.closest && <p className={clsx("mt-1 text-xs text-muted")}>Closest past stress: {r.closest.name}{r.closest.family != null && <> (the family would have moved {pct(r.closest.family, 1)})</>}.</p>}
            </li>
          ))}</ul>
          <p className="mt-2 text-xs text-faint">The smallest combination of factor moves, measured in standard deviations of a normal month, that produces the loss. Correlations between factors are included.</p>
        </div>
      )}
    </section>
  );
}
