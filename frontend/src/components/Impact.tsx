import { CaretDown, Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { Fragment, useMemo, useState } from "react";
import type { ImpactEdit, ImpactResult, ImpactStats } from "../lib/api";
import { amt, dateLong, pct } from "../lib/format";
import { Avatar } from "./Avatar";
import { FACTOR_LABEL, Report } from "./Researched";
import { InfoTip, Signed } from "./ui";

export type RegimeChoice = "auto" | "normal" | "selloff";
const fmt = (v: number | null | undefined, signed = true) => amt(v, signed);
const HORIZON: Record<string, string> = { "1w": "1 week", "2w": "2 weeks", "1m": "1 month", "3m": "3 months", "6m": "6 months", "12m": "12 months" };
const label = (f: string) => FACTOR_LABEL[f] ?? (f.startsWith("sector_") ? `${f.slice(7)} sector` : f);

/** Level 1 answer, branch cards and the per-person table; levels 2 and 3 open on demand. */
export function Impact({ r, regime, setRegime, onEdit, busy }: {
  r: ImpactResult; regime: RegimeChoice; setRegime: (v: RegimeChoice) => void; onEdit: (e: ImpactEdit) => void; busy?: boolean;
}) {
  const [view, setView] = useState<string>("family");
  const [level, setLevel] = useState<0 | 2 | 3>(0);
  const many = r.blended.length > 2;
  const who = many ? "the family" : r.blended[1]?.name ?? "the portfolio";
  const verb = (v: number) => (v < 0 ? "loses" : "gains");
  return (
    <div className={clsx("mt-10 transition-opacity duration-fast", busy && "opacity-60")}>
      {/* Level 1 */}
      <p className="max-w-[60ch] text-2xl font-medium leading-snug md:text-3xl">
        Most likely: {who} {verb(r.answer.median_amt)} about <Signed value={r.answer.median_amt}>{fmt(Math.abs(r.answer.median_amt), false)}</Signed>{" "}
        (<Signed value={r.answer.median}>{pct(r.answer.median, 1)}</Signed>).{" "}
        <span className="text-muted">Bad case (1 in 20): <Signed value={r.answer.p5_amt}>{fmt(r.answer.p5_amt)}</Signed>.</span>
      </p>
      <p className="mt-2 text-sm text-muted">
        Over {HORIZON[r.horizon] ?? r.horizon}. Stock performance only, currency effects excluded. Chance of a loss: {pct(r.answer.prob_loss, 0, false)}.
        <InfoTip text={`From ${r.n.toLocaleString("en-US")} simulated outcomes: which branch happens, how big each move is, and each holding's own noise. "Most likely" is the middle outcome; the bad case is the 5th percentile.`} />
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted">Correlations:</span>
        {(["auto", "normal", "selloff"] as RegimeChoice[]).map((v) => (
          <button key={v} type="button" aria-pressed={regime === v} onClick={() => setRegime(v)}
            className={clsx("press h-8 rounded-sm border px-3", regime === v ? "border-accent bg-accent-soft font-medium" : "border-line hover:bg-hover")}>
            {v === "auto" ? "By cause" : v === "normal" ? "Normal markets" : "Sell-off"}
          </button>
        ))}
        <InfoTip text="How factors move together. By cause: sell-off weeks for crisis causes (geopolitical, supply, demand), normal weeks otherwise." />
      </div>

      <ul className="-mx-4 mt-6 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-2 md:mx-0 md:grid md:grid-cols-[repeat(auto-fit,minmax(220px,1fr))] md:overflow-visible md:px-0">
        {r.branches.map((b) => {
          const f = b.books[0];
          return (
            <li key={b.name} className="min-w-[78%] snap-start rounded-md border border-line p-4 sm:min-w-[46%] md:min-w-0">
              <div className="flex items-baseline justify-between gap-2"><p className="font-medium">{b.name}</p><p className="num text-lg font-semibold">{Math.round(b.probability * 100)}%</p></div>
              <p className="text-xs text-faint">{b.cause}{b.regime === "selloff" ? " · sell-off correlations" : ""}</p>
              <p className="mt-3"><Signed value={f.median} className="text-xl font-semibold">{pct(f.median, 1)}</Signed> <Signed value={f.median_amt} className="text-sm">{fmt(f.median_amt)}</Signed></p>
              <p className="num text-xs text-muted">{pct(f.p5, 1)} to {pct(f.p95, 1)}</p>
            </li>
          );
        })}
      </ul>

      <BookTable r={r} view={view} setView={setView} />
      <Holdings r={r} view={view} />

      <div className="mt-10 flex flex-wrap gap-2">
        <button type="button" className="btn-secondary" aria-expanded={level >= 2} onClick={() => setLevel(level >= 2 ? 0 : 2)}>
          See the analysis<CaretDown size={14} className={clsx("transition-transform duration-fast", level >= 2 && "rotate-180")} /></button>
        <button type="button" className="btn-ghost" aria-expanded={level === 3} onClick={() => setLevel(level === 3 ? 0 : 3)}>Model details</button>
      </div>
      {level >= 2 && <Analysis r={r} onEdit={onEdit} />}
      {level === 3 && <Details r={r} />}
    </div>
  );
}

function BookTable({ r, view, setView }: { r: ImpactResult; view: string; setView: (v: string) => void }) {
  const rows = r.blended;
  return (
    <div className="mt-8 overflow-x-auto">
      <table className="w-full min-w-[560px] border-separate border-spacing-0 text-sm">
        <thead><tr className="text-xs text-muted">
          {["", "Most likely", "", "Bad case (1 in 20)", "Good case", "Chance of loss"].map((h, i) => (
            <th key={i} className={clsx("border-b border-line py-2 font-medium", i === 0 ? "text-left" : "pl-3 text-right")}>{h}</th>))}
        </tr></thead>
        <tbody>{rows.map((b: ImpactStats, i) => {
          const key = b.id == null ? "family" : String(b.id);
          return (
            <tr key={key} tabIndex={0} onClick={() => setView(key)} onKeyDown={(e) => { if (e.key === "Enter") setView(key); }}
              className={clsx("cursor-pointer transition-colors duration-fast hover:bg-hover", view === key && "bg-hover")}>
              <td className={clsx("border-b border-line py-2.5", i === 0 && "font-semibold")}>{b.name}</td>
              <td className="num border-b border-line py-2.5 pl-3 text-right"><Signed value={b.median}>{pct(b.median, 1)}</Signed></td>
              <td className="num border-b border-line py-2.5 pl-3 text-right"><Signed value={b.median_amt}>{fmt(b.median_amt)}</Signed></td>
              <td className="num border-b border-line py-2.5 pl-3 text-right"><Signed value={b.p5_amt}>{fmt(b.p5_amt)}</Signed> <span className="text-xs text-faint">{pct(b.p5, 1)}</span></td>
              <td className="num border-b border-line py-2.5 pl-3 text-right"><Signed value={b.p95_amt}>{fmt(b.p95_amt)}</Signed></td>
              <td className="num border-b border-line py-2.5 pl-3 text-right">{pct(b.prob_loss, 0, false)}</td>
            </tr>
          );
        })}</tbody>
      </table>
      <p className="mt-2 text-xs text-faint">Click a row to see that person's holdings and what drives them.</p>
    </div>
  );
}

function Holdings({ r, view }: { r: ImpactResult; view: string }) {
  const [all, setAll] = useState(false);
  const rows = r.holdings[view] ?? [];
  const name = r.blended.find((b) => (b.id == null ? "family" : String(b.id)) === view)?.name;
  const shown = all ? rows : rows.slice(0, 10);
  if (!rows.length) return null;
  return (
    <div className="mt-8">
      <h3 className="text-base font-semibold">{name}: holdings</h3>
      <p className="mt-1 text-xs text-muted">Expected move (probability-weighted middle case) and the factors behind it.</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[520px] border-separate border-spacing-0 text-sm">
          <thead><tr className="text-xs text-muted">
            <th className="border-b border-line py-2 text-left font-medium">Holding</th><th className="border-b border-line py-2 pl-3 text-right font-medium">Move</th>
            <th className="border-b border-line py-2 pl-3 text-right font-medium">Amount</th><th className="border-b border-line py-2 pl-4 text-left font-medium">Why</th>
          </tr></thead>
          <tbody>{shown.map((h) => (
            <tr key={h.symbol}>
              <td className="border-b border-line py-2"><span className="flex items-center gap-2"><Avatar ticker={h.symbol} size={20} /><span className="font-mono font-semibold">{h.symbol}</span>
                <span className="text-xs text-faint">{pct(h.weight, 1, false)}</span></span></td>
              <td className="num border-b border-line py-2 pl-3 text-right"><Signed value={h.move}>{pct(h.move, 1)}</Signed></td>
              <td className="num border-b border-line py-2 pl-3 text-right"><Signed value={h.change_amt}>{fmt(h.change_amt)}</Signed></td>
              <td className="border-b border-line py-2 pl-4 text-xs text-muted">
                {h.own_news && <span className="mr-2 font-medium text-text">company news</span>}
                {h.why.map((w) => `${label(w.factor)} ${pct(w.effect, 1)}`).join(", ")}
              </td>
            </tr>))}</tbody>
        </table>
      </div>
      {rows.length > 10 && <button type="button" className="btn-ghost -ml-2 mt-1" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>}
    </div>
  );
}

// ------------------------------------------------------------------ level 2

function Analysis({ r, onEdit }: { r: ImpactResult; onEdit: (e: ImpactEdit) => void }) {
  const signposts = r.spec.branches.flatMap((b) => (b.signposts ?? []).map((s) => ({ ...s, branch: b.name })));
  return (
    <section className="fade-in mt-8 space-y-12" aria-label="Analysis">
      <div>
        <h3 className="text-base font-semibold">How it spreads</h3>
        <p className="mt-1 text-xs text-muted">Edit any move: everything downstream of it in the chain scales with it and the results recalculate.</p>
        <div className="mt-4 space-y-8">{r.spec.branches.map((b) => <Flow key={b.name} branch={b} onEdit={onEdit} />)}</div>
      </div>
      <Histogram r={r} />
      {r.analogs && r.analogs.length > 0 && (
        <div>
          <h3 className="text-base font-semibold">Past analogs, replayed on today's holdings</h3>
          <ul className="mt-3 grid gap-x-8 gap-y-5 md:grid-cols-2">
            {r.analogs.map((a, i) => (
              <li key={i} className="border-t border-line pt-3">
                <div className="flex items-baseline justify-between gap-3">
                  <div><p className="font-medium">{a.name} <span className="text-xs font-normal text-faint">{a.branch}</span></p>
                    <p className="text-xs text-faint">{dateLong(a.start)} to {dateLong(a.end)}</p></div>
                  {a.no_data ? <span className="text-xs text-faint">no price data</span>
                    : <div className="text-right"><Signed value={a.family.impact} className="text-lg font-semibold">{pct(a.family.impact, 1)}</Signed></div>}
                </div>
                {!a.no_data && <p className="mt-1.5 flex flex-wrap gap-x-3 text-xs text-muted">{a.factors.map((f) => <span key={f.symbol}>{f.label.replace(/ \(.*\)$/, "")} <Signed value={f.move}>{pct(f.move, 1)}</Signed></span>)}</p>}
              </li>
            ))}
          </ul>
          {r.warnings.map((w) => <p key={w} className="mt-3 flex items-start gap-2 text-sm"><Warning size={16} className="mt-0.5 shrink-0 text-accent" />{w}</p>)}
        </div>
      )}
      {signposts.length > 0 && (
        <div>
          <h3 className="text-base font-semibold">Signposts</h3>
          <ul className="mt-2 border-t border-line text-sm">{signposts.map((s, i) => (
            <li key={i} className="flex flex-wrap justify-between gap-x-4 border-b border-line py-2"><span><span className="font-medium">{s.indicator}</span> <span className="num">{s.condition} {s.value}</span></span>
              <span className="text-muted">{s.meaning} <span className="text-xs text-faint">({s.branch})</span></span></li>))}</ul>
        </div>
      )}
      {r.spec.sources?.length ? (
        <div><h3 className="text-base font-semibold">Sources</h3>
          <ul className="mt-2 space-y-1 text-sm">{r.spec.sources.map((x) => <li key={x.url}><a className="link" href={x.url} target="_blank" rel="noreferrer noopener">{x.title}</a></li>)}</ul></div>
      ) : null}
    </section>
  );
}

function Flow({ branch, onEdit }: { branch: ImpactResult["spec"]["branches"][number]; onEdit: (e: ImpactEdit) => void }) {
  const cols = useMemo(() => {
    const links = branch.transmission ?? [];
    const depth: Record<string, number> = {};
    const moves = Object.keys(branch.factor_moves);
    for (const l of links) if (l.from === "trigger") depth[l.to] = 1;
    for (let pass = 0; pass < 6; pass++) for (const l of links) if (depth[l.from] && (depth[l.to] ?? 0) < depth[l.from] + 1) depth[l.to] = depth[l.from] + 1;
    for (const m of moves) if (!depth[m]) depth[m] = 1;  // named moves outside the chain
    const max = Math.max(1, ...Object.values(depth));
    return Array.from({ length: max }, (_, i) => Object.keys(depth).filter((k) => depth[k] === i + 1 && branch.factor_moves[k]));
  }, [branch]);
  const into = (f: string) => (branch.transmission ?? []).filter((l) => l.to === f);
  return (
    <div>
      <p className="text-sm font-medium">{branch.name} <span className="text-xs font-normal text-faint">{Math.round(branch.probability * 100)}%</span></p>
      <div className="mt-2 flex items-stretch gap-2 overflow-x-auto pb-2">
        <div className="flex shrink-0 items-center rounded-md border border-dashed border-line-strong px-3 py-2 text-sm text-muted">Trigger</div>
        {cols.map((col, i) => (
          <Fragment key={i}>
            <div className="flex shrink-0 items-center text-faint" aria-hidden>→</div>
            <div className="flex shrink-0 flex-col justify-center gap-2">
              {col.map((f) => {
                const m = branch.factor_moves[f];
                return (
                  <div key={f} className="rounded-md border border-line bg-surface px-3 py-2">
                    <p className="text-xs text-muted">{label(f)}</p>
                    <EditMove value={m.mid} onCommit={(v) => onEdit({ branch: branch.name, factor: f, mid: v })} />
                    <p className="num text-[11px] text-faint">{m.low > 0 ? "+" : ""}{m.low.toFixed(1)} to {m.high > 0 ? "+" : ""}{m.high.toFixed(1)}%</p>
                    {into(f).length > 0 && <p className="text-[11px] text-faint">from {into(f).map((l) => (l.from === "trigger" ? "trigger" : label(l.from))).join(", ")}</p>}
                  </div>
                );
              })}
            </div>
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function EditMove({ value, onCommit }: { value: number; onCommit: (v: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft == null) return;
    const v = Number(draft.replace("−", "-").replace("%", ""));
    if (isFinite(v) && v !== value) onCommit(v);
    setDraft(null);
  };
  return (
    <input aria-label="Move in percent" inputMode="decimal" className={clsx("num w-20 rounded-sm border border-transparent bg-transparent text-base font-semibold outline-none hover:border-line focus:border-accent",
      value < 0 ? "text-loss" : value > 0 ? "text-gain" : "")}
      value={draft ?? `${value > 0 ? "+" : ""}${value.toFixed(1)}%`} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
  );
}

function Histogram({ r }: { r: ImpactResult }) {
  const { edges, counts } = r.histogram;
  const max = Math.max(...counts, 1);
  const f = r.blended[0];
  const w = 600, h = 140;
  const x = (v: number) => ((v - edges[0]) / (edges[edges.length - 1] - edges[0] || 1)) * w;
  const bw = w / counts.length;
  return (
    <div>
      <h3 className="text-base font-semibold">Range of outcomes for {r.blended.length > 2 ? "the family" : f.name}</h3>
      <svg viewBox={`0 0 ${w} ${h + 24}`} className="mt-3 w-full max-w-[640px]" role="img" aria-label={`Histogram of ${r.n} simulated outcomes`}>
        {counts.map((c, i) => {
          const mid = (edges[i] + edges[i + 1]) / 2;
          return <rect key={i} x={i * bw + 0.5} y={h - (c / max) * h} width={bw - 1} height={(c / max) * h} className={mid < 0 ? "fill-loss" : "fill-gain"} opacity={0.55} />;
        })}
        {[["5%", f.p5], ["Median", f.median], ["95%", f.p95]].map(([t, v]) => (
          <g key={t as string}>
            <line x1={x(v as number)} x2={x(v as number)} y1={0} y2={h} className="stroke-text" strokeWidth={1} strokeDasharray={t === "Median" ? "" : "3 3"} />
            <text x={x(v as number)} y={h + 16} textAnchor="middle" className="fill-muted text-[11px]">{t} {pct(v as number, 1)}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

// ------------------------------------------------------------------ level 3

function Details({ r }: { r: ImpactResult }) {
  const e = r.exposures.family;
  return (
    <section className="fade-in mt-8 space-y-8 text-sm" aria-label="Model details">
      <div>
        <h3 className="text-base font-semibold">Exposures used ({r.blended.length > 2 ? "family" : r.blended[1]?.name})</h3>
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-muted">
          {Object.entries(e).filter(([, v]) => Math.abs(v) >= 0.02).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([k, v]) => (
            <span key={k}>{label(k)} <span className="num text-text">{v.toFixed(2)}</span></span>))}
        </p>
      </div>
      <div>
        <h3 className="text-base font-semibold">Regime</h3>
        <ul className="mt-2 text-muted">{r.branches.map((b) => <li key={b.name}>{b.name}: {b.regime === "selloff" ? "sell-off" : "normal"} correlations ({b.cause}) · {b.draws.toLocaleString("en-US")} draws</li>)}</ul>
      </div>
      <div>
        <h3 className="text-base font-semibold">Assumptions</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">{r.assumptions.map((a) => <li key={a}>{a}</li>)}<li>Seed {r.seed}: the same inputs always give the same numbers.</li></ul>
      </div>
      {r.report && <div><h3 className="text-base font-semibold">Validation</h3><Report report={r.report} /></div>}
    </section>
  );
}
