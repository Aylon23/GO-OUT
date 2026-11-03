import { CheckCircle, WarningCircle } from "@phosphor-icons/react";
import clsx from "clsx";
import { Fragment, useEffect, useState } from "react";
import { api, ApiError, type ContextPack, type Multiple, type StockPack } from "../lib/api";
import { dateLong, num, pct } from "../lib/format";
import { AiQuota } from "./AiQuota";
import { ErrorLine, Signed, Skel } from "./ui";

const FACTORS = ["market", "size", "value", "momentum", "quality", "rates", "oil", "gold"];
const p1 = (v: number | null | undefined) => (v == null ? "–" : pct(v, 1));
const missing = <span className="text-faint">missing</span>;

/** The context pack exactly as it would be sent to the models (tickers, weights, betas; no names or amounts). */
export function ContextPackView() {
  const [ticker, setTicker] = useState<string | null>(null);
  const [d, setD] = useState<ContextPack | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [raw, setRaw] = useState(false);
  useEffect(() => {
    let live = true;
    setBusy(true); setErr(null);
    api.aiContextPack(ticker ? [ticker] : []).then((r) => {
      if (!live) return;
      setD(r);
      if (ticker == null && r.pack.family.holdings.length) setTicker(r.pack.family.holdings.find((h) => h.betas)?.ticker ?? r.pack.family.holdings[0].ticker);
    }).catch((e) => live && setErr(e instanceof ApiError ? e.message : "Couldn't build it.")).finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [ticker]);
  if (err) return <ErrorLine message={err} />;
  if (!d) return <div className="mt-4 space-y-2" aria-busy><Skel className="h-6 w-60" /><Skel className="h-40 w-full" /></div>;
  const { pack, privacy } = d;
  const stock = pack.stocks[0];
  return (
    <div className="mt-5 space-y-8" aria-busy={busy}>
      <div className={clsx("flex flex-wrap items-start justify-between gap-3 rounded-sm border p-3 text-sm", privacy.ok ? "border-line" : "border-loss")}>
        <p className="flex items-start gap-2">
          {privacy.ok ? <CheckCircle size={18} className="mt-0.5 shrink-0 text-gain" weight="fill" /> : <WarningCircle size={18} className="mt-0.5 shrink-0 text-loss" weight="fill" />}
          <span>{privacy.ok ? "Privacy check passed: no names or amounts." : `Would not be sent: ${privacy.problems.join("; ")}.`}
            <span className="block text-xs text-muted">Checked against {privacy.checked_names} names and nicknames and {privacy.checked_amounts.toLocaleString()} amounts. {(d.bytes / 1024).toFixed(1)} KB · <span className="font-mono">{d.hash.slice(0, 12)}</span></span></span>
        </p>
        <AiQuota />
      </div>
      <p className="text-xs text-muted">{pack.basis} As of {dateLong(pack.today)}.</p>

      <section aria-label="Market snapshot">
        <h3 className="text-sm font-semibold">Market snapshot</h3>
        <div className="mt-2 overflow-x-auto"><table className="w-full min-w-[560px] text-xs">
          <thead><tr className="text-muted">{["", "Level", "1 month", "1 year", "1-year range", "As of"].map((h, i) => <th key={i} className={clsx("border-b border-line py-1.5 font-medium", i ? "pl-3 text-right" : "text-left")}>{h}</th>)}</tr></thead>
          <tbody>{pack.market.map((m) => {
            const pts = m.symbol === "^TNX";
            return (
              <tr key={m.symbol}>
                <td className="border-b border-line py-1.5">{m.name} <span className="font-mono text-faint">{m.symbol}</span></td>
                <td className="num border-b border-line py-1.5 pl-3 text-right">{m.level == null ? missing : num(m.level, 2) + (pts ? "%" : "")}</td>
                <td className="num border-b border-line py-1.5 pl-3 text-right">{pts ? ptsText(m.change_1m_pts) : <Signed value={m.change_1m ?? null}>{p1(m.change_1m)}</Signed>}</td>
                <td className="num border-b border-line py-1.5 pl-3 text-right">{pts ? ptsText(m.change_1y_pts) : <Signed value={m.change_1y ?? null}>{p1(m.change_1y)}</Signed>}</td>
                <td className="num border-b border-line py-1.5 pl-3 text-right">{m.low_1y != null ? `${num(m.low_1y, 2)} – ${num(m.high_1y, 2)}` : "–"}</td>
                <td className="num border-b border-line py-1.5 pl-3 text-right text-muted">{m.as_of ? dateLong(m.as_of) : missing}</td>
              </tr>
            );
          })}</tbody>
        </table></div>
      </section>

      <section aria-label="Family holdings">
        <h3 className="text-sm font-semibold">Family holdings <span className="font-normal text-muted">· tickers, combined weights and factor betas</span></h3>
        <div className="mt-2 max-h-[340px] overflow-auto"><table className="w-full min-w-[620px] text-xs">
          <thead className="sticky top-0 bg-surface"><tr className="text-muted">{["Ticker", "Weight", ...FACTORS, "R²"].map((h, i) => <th key={h} className={clsx("border-b border-line py-1.5 font-medium capitalize", i ? "pl-2 text-right" : "text-left")}>{h}</th>)}</tr></thead>
          <tbody>{pack.family.holdings.map((h) => (
            <tr key={h.ticker}>
              <td className="border-b border-line py-1 font-mono font-semibold">{h.ticker}</td>
              <td className="num border-b border-line py-1 pl-2 text-right">{pct(h.weight, 1, false)}</td>
              {FACTORS.map((f) => <td key={f} className="num border-b border-line py-1 pl-2 text-right">{h.betas?.[f] != null ? h.betas[f].toFixed(2) : "–"}</td>)}
              <td className="num border-b border-line py-1 pl-2 text-right text-muted">{h.r2 != null ? h.r2.toFixed(2) : "–"}</td>
            </tr>
          ))}</tbody>
        </table></div>
        <p className="mt-1.5 text-xs text-muted">Cash {p1(pack.family.cash_weight)} of the family total. Family betas: {FACTORS.map((f) => `${f} ${(pack.family.family_betas[f] ?? 0).toFixed(2)}`).join(" · ")}</p>
      </section>

      {pack.analogs && (
        <section aria-label="Analog library">
          <h3 className="text-sm font-semibold">Analog library <span className="font-normal text-muted">· real moves from our price data, in percent</span></h3>
          <div className="mt-2 max-h-[340px] overflow-auto"><table className="w-full min-w-[720px] text-xs">
            <thead className="sticky top-0 bg-surface"><tr className="text-muted">{["Window", "S&P 500", "Brent", "Market (SPY)", "Rates (TLT)", "Oil (USO)", "Gold (GLD)"].map((h, i) => <th key={i} className={clsx("border-b border-line py-1.5 font-medium", i ? "pl-2 text-right" : "text-left")}>{h}</th>)}</tr></thead>
            <tbody>{pack.analogs.map((a) => (
              <tr key={a.id}>
                <td className="border-b border-line py-1">{a.name}<span className="block text-faint">{dateLong(a.start)} – {dateLong(a.end)}</span></td>
                {[a.market_moves_pct.sp500, a.market_moves_pct.brent, a.factor_moves_pct.market, a.factor_moves_pct.rates, a.factor_moves_pct.oil, a.factor_moves_pct.gold].map((v, i) => (
                  <td key={i} className="num border-b border-line py-1 pl-2 text-right">{v == null ? missing : <Signed value={v}>{v > 0 ? "+" : v < 0 ? "−" : ""}{Math.abs(v).toFixed(1)}</Signed>}</td>
                ))}
              </tr>
            ))}</tbody>
          </table></div>
        </section>
      )}

      <section aria-label="Stock data pack">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold">Stock data pack</h3>
          <select aria-label="Stock" className="field h-8 w-auto py-0 text-sm" value={ticker ?? ""} onChange={(e) => setTicker(e.target.value)}>
            {pack.family.holdings.map((h) => <option key={h.ticker} value={h.ticker}>{h.ticker}</option>)}
          </select>
          {busy && <span className="text-xs text-muted">Building…</span>}
        </div>
        {stock ? <StockView s={stock} /> : <p className="mt-2 text-xs text-faint">Pick a holding.</p>}
      </section>

      <div>
        <button type="button" className="btn-ghost h-7 text-xs" onClick={() => setRaw((v) => !v)} aria-expanded={raw}>{raw ? "Hide raw JSON" : "Show raw JSON"}</button>
        {raw && <pre className="mt-2 max-h-[420px] overflow-auto rounded-sm bg-sunken p-3 font-mono text-[11px] leading-relaxed">{JSON.stringify(pack, null, 2)}</pre>}
      </div>
    </div>
  );
}

function ptsText(v: number | null | undefined) {
  if (v == null) return "–";
  return <Signed value={v}>{v > 0 ? "+" : v < 0 ? "−" : ""}{Math.abs(v).toFixed(2)} pts</Signed>;
}

function StockView({ s }: { s: StockPack }) {
  const v = s.valuation;
  const row = (k: string, m: Multiple) => (
    <tr key={k}>
      <td className="border-b border-line py-1">{k}</td>
      <td className="num border-b border-line py-1 pl-3 text-right">{m.value != null ? m.value.toFixed(1) : missing}</td>
      <td className="num border-b border-line py-1 pl-3 text-right">{m.own_5y_percentile != null ? `${Math.round(m.own_5y_percentile * 100)}th` : missing}</td>
      <td className="num border-b border-line py-1 pl-3 text-right">{m.sector_median != null ? m.sector_median.toFixed(1) : missing}</td>
    </tr>
  );
  return (
    <div className="mt-3 grid gap-6 text-xs md:grid-cols-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
        <dt className="text-muted">Company</dt><dd>{s.name ?? s.ticker}{s.sector ? ` · ${s.sector}` : ""}</dd>
        <dt className="text-muted">Price</dt><dd className="num">{s.price != null ? `${num(s.price, 2)} ${s.currency}` : missing}{s.as_of && <span className="text-faint"> · {dateLong(s.as_of)}</span>}</dd>
        <dt className="text-muted">52-week range</dt><dd className="num">{s.range_52w ? `${num(s.range_52w.low, 2)} – ${num(s.range_52w.high, 2)}` : missing}{s.from_high != null && <span className="text-muted"> · {pct(s.from_high, 1)} from the high</span>}</dd>
        {(["3m", "6m", "12m"] as const).map((k) => {
          const r = s.returns?.[k];
          return <Fragment key={k}><dt className="text-muted">{k.replace("m", " months")}</dt>
            <dd className="num">{r?.stock != null ? <>{pct(r.stock, 1)} <span className="text-muted">vs SPY {p1(r.spy)} → </span><Signed value={r.vs_spy}>{p1(r.vs_spy)}</Signed></> : missing}</dd></Fragment>;
        })}
        <dt className="text-muted">Analysts</dt><dd>{s.analysts ? <>{s.analysts.consensus?.replace("_", " ") ?? "–"} · {s.analysts.analysts ?? "?"} analysts · target {s.analysts.mean_target != null ? num(s.analysts.mean_target, 2) : "–"} ({p1(s.analysts.upside)})</> : missing}</dd>
        <dt className="text-muted">Revisions (this year)</dt><dd className="num">{s.revisions?.["0y"] ? <>{s.revisions["0y"].up_30d ?? 0} up / {s.revisions["0y"].down_30d ?? 0} down in 30 days · estimate {p1(s.revisions["0y"].estimate_change_30d)}</> : missing}</dd>
        <dt className="text-muted">Next earnings</dt><dd className="num">{s.next_earnings ? `${dateLong(s.next_earnings.date)} (in ${s.next_earnings.days_away} days)` : missing}</dd>
        <dt className="text-muted">ROE · debt/equity</dt><dd className="num">{p1(s.roe)} · {s.debt_to_equity != null ? s.debt_to_equity.toFixed(2) : "–"}</dd>
        <dt className="text-muted">Dividend yield · beta</dt><dd className="num">{p1(s.dividend_yield)} · {s.beta != null ? s.beta.toFixed(2) : "–"}</dd>
      </dl>
      <div className="space-y-4">
        <table className="w-full">
          <thead><tr className="text-muted">{["Valuation", "Now", "Own 5-yr pct.", "Sector median"].map((h, i) => <th key={h} className={clsx("border-b border-line py-1.5 font-medium", i ? "pl-3 text-right" : "text-left")}>{h}</th>)}</tr></thead>
          <tbody>{row("Trailing P/E", v.trailing_pe)}{row("Forward P/E", v.forward_pe)}{row("EV / sales", v.ev_sales)}</tbody>
        </table>
        <p className="text-faint">{v.history_note}. Peers: {v.peers.join(", ") || "none"}.</p>
        <table className="w-full">
          <thead><tr className="text-muted">{["Growth (YoY)", ...s.growth.revenue_yoy_last4q.map((q) => q.quarter_end.slice(0, 7)), "Next yr est."].map((h, i) => <th key={i} className={clsx("border-b border-line py-1.5 font-medium", i ? "pl-3 text-right" : "text-left")}>{h}</th>)}</tr></thead>
          <tbody>
            <tr><td className="border-b border-line py-1">Revenue</td>{s.growth.revenue_yoy_last4q.map((q) => <td key={q.quarter_end} className="num border-b border-line py-1 pl-3 text-right">{p1(q.yoy)}</td>)}<td className="num border-b border-line py-1 pl-3 text-right">{p1(s.growth.next_year_revenue_growth_est)}</td></tr>
            <tr><td className="border-b border-line py-1">EPS</td>{s.growth.eps_yoy_last4q.map((q) => <td key={q.quarter_end} className="num border-b border-line py-1 pl-3 text-right">{p1(q.yoy)}</td>)}<td className="num border-b border-line py-1 pl-3 text-right">{p1(s.growth.next_year_eps_growth_est)}</td></tr>
            {s.margins && <tr><td className="border-b border-line py-1">Net margin</td>{s.margins.net.map((m, i) => <td key={i} className="num border-b border-line py-1 pl-3 text-right">{p1(m)}</td>)}<td className="border-b border-line py-1 pl-3 text-right text-muted">{s.margins.trend.net ?? "–"}</td></tr>}
          </tbody>
        </table>
        {s.factor_exposures && <p className="num text-muted">Betas: {FACTORS.map((f) => `${f} ${(s.factor_exposures!.betas[f] ?? 0).toFixed(2)}`).join(" · ")} · R² {s.factor_exposures.r2?.toFixed(2) ?? "–"}</p>}
        <p className={clsx(s.missing.length ? "text-muted" : "text-faint")}>Missing: {s.missing.length ? s.missing.join(", ") : "nothing"}</p>
      </div>
    </div>
  );
}
