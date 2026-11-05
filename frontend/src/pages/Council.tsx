import { Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { AiQuota } from "../components/AiQuota";
import { Avatar } from "../components/Avatar";
import { StockView } from "../components/ContextPackView";
import { ErrorLine, Segmented, SidePanel, Signed, Skel, Blank } from "../components/ui";
import { api, ApiError, type CouncilOverview, type CouncilStock, type CouncilVerdictRow, type Rating } from "../lib/api";
import { dateLong, num, pct } from "../lib/format";
import { useData } from "../lib/hooks";

const R_LABEL: Record<Rating, string> = { buy: "Buy", hold: "Hold", sell: "Sell", insufficient: "Insufficient" };
const R_TONE: Record<string, string> = { buy: "bg-gain", hold: "bg-line-strong", sell: "bg-loss", insufficient: "bg-sunken" };
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
type SortKey = "ticker" | "tally" | "consensus" | "conviction" | "upside" | "from_high";

/** Council: the AI models' Buy / Hold / Sell on every holding and the watchlist, with the raw tally. */
export default function Council() {
  const q = useData(api.council, "council");
  const [d, setD] = useState<CouncilOverview | null>(null);
  const [sort, setSort] = useState<SortKey>("tally");
  const [dir, setDir] = useState<1 | -1>(-1);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const data = d ?? q.data;
  const running = data?.run && ["preparing", "running"].includes(data.run.status);
  useEffect(() => {  // poll while a run is going
    if (!running) return;
    const t = setInterval(async () => { try { setD(await api.council()); } catch { /* keep the last view */ } }, 5000);
    return () => clearInterval(t);
  }, [running]);
  const rows = useMemo(() => {
    const r = [...(data?.rows ?? [])];
    const score = (x: CouncilVerdictRow): number | string => {
      switch (sort) {
        case "ticker": return x.ticker;
        case "tally": return (x.tally.buy - x.tally.sell) * 10 + x.tally.hold;
        case "consensus": return { unanimous: 3, strong: 2, split: 1, insufficient: 0 }[x.consensus];
        case "conviction": return x.conviction ?? -1;
        case "upside": return x.upside ?? -9;
        case "from_high": return x.from_high ?? -9;
      }
    };
    r.sort((a, b) => { const A = score(a), B = score(b); return (A < B ? -1 : A > B ? 1 : 0) * dir; });
    return r;
  }, [data, sort, dir]);
  if (q.error && !data) return <Page><ErrorLine message={q.error.message} onRetry={q.reload} /></Page>;
  if (!data) return <Page><Skel className="h-8 w-60" /><Skel className="mt-6 h-64 w-full" /></Page>;
  const runNow = async () => { setErr(null); try { await api.councilRun(); setD(await api.council()); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't start it."); } };
  const head = (k: SortKey, label: string, right = true) => (
    <th scope="col" aria-sort={sort === k ? (dir > 0 ? "ascending" : "descending") : "none"} className={clsx("border-b border-line py-2 font-medium", right ? "pl-3 text-right" : "text-left")}>
      <button type="button" className="hover:text-text" onClick={() => { if (sort === k) setDir((x) => (x > 0 ? -1 : 1)); else { setSort(k); setDir(-1); } }}>
        {label}{sort === k ? (dir > 0 ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
  const r = data.run;
  return (
    <Page>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Council</h1>
          <p className="mt-1 max-w-[66ch] text-sm text-muted">Four AI models rate each holding against SPY over 12 months, then challenge each other. The tally is never averaged into one score.</p>
          <p className="mt-1 text-xs text-faint">{data.label}</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <AiQuota />
          <button type="button" className="btn-primary" disabled={!!running} onClick={runNow}>{running ? "Running…" : "Run the council now"}</button>
        </div>
      </div>
      {err && <ErrorLine message={err} />}
      {r && (
        <p className="mt-4 text-sm" aria-live="polite">
          {running ? <>{r.rated} of {r.total} rated</> : r.status === "done" ? <>Last run {dateLong(r.created_at.slice(0, 10))}: {r.total} stocks</> : <>Last run stopped</>}
          <span className="text-muted"> · {r.requests} requests · devil's advocate: Model {r.devil}</span>
          {r.waiting && <span className="block text-muted">{r.waiting}</span>}
        </p>
      )}
      <Settings d={data} onChange={setD} />

      {rows.length === 0 ? <p className="mt-10 text-sm text-muted">No ratings yet. Run the council to rate every holding and the watchlist.</p> : (
        <div className="mt-6 overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead><tr className="text-xs text-muted">
              {head("ticker", "Stock", false)}
              {head("tally", "Tally")}
              {head("consensus", "Consensus")}
              <th scope="col" className="border-b border-line py-2 pl-3 text-right font-medium">Agreement</th>
              <th scope="col" className="border-b border-line py-2 pl-3 text-right font-medium">Change</th>
              {head("conviction", "Conviction")}
              {head("upside", "To analyst target")}
              {head("from_high", "From 52-week high")}
            </tr></thead>
            <tbody>{rows.map((x) => (
              <tr key={x.ticker} className="cursor-pointer hover:bg-hover" onClick={() => setOpen(x.ticker)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") setOpen(x.ticker); }}>
                <td className="border-b border-line py-2"><span className="flex items-center gap-2"><Avatar ticker={x.ticker} size={24} /><span className="font-semibold">{x.ticker}</span>
                  {x.crowded && <span title="Crowded: strong agreement near the 52-week extreme, with analysts already there" className="inline-flex items-center gap-0.5 text-xs text-loss"><Warning size={12} weight="fill" />crowded</span>}</span></td>
                <td className="border-b border-line py-2 pl-3"><TallyBar t={x.tally} majority={x.majority} /></td>
                <td className="border-b border-line py-2 pl-3 text-right">{x.consensus}{x.flipflops.length > 0 && <span className="block text-xs text-faint">{x.flipflops.length} flip-flop{x.flipflops.length > 1 ? "s" : ""}</span>}</td>
                <td className="border-b border-line py-2 pl-3 text-right text-muted">{x.quality || <Blank why="No quality rating returned for this stock." />}</td>
                <td className="border-b border-line py-2 pl-3 text-right">{x.change || <span className="text-faint">–</span>}</td>
                <td className="num border-b border-line py-2 pl-3 text-right">{x.conviction != null ? x.conviction.toFixed(1) : <Blank why="Not enough council votes to score conviction." />}</td>
                <td className="num border-b border-line py-2 pl-3 text-right">{x.upside != null ? <Signed value={x.upside}>{pct(x.upside, 1)}</Signed> : <Blank why="No price target from the council for this stock." />}</td>
                <td className="num border-b border-line py-2 pl-3 text-right">{x.from_high != null ? pct(x.from_high, 1) : <Blank why="Less than a year of prices." />}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <Board />
      <SidePanel open={open != null} onClose={() => setOpen(null)} title={open ? `${open}: the council` : ""} wide="xl">
        {open && <StockCouncil ticker={open} />}
      </SidePanel>
    </Page>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">{children}</div>;
}

export function TallyBar({ t, majority }: { t: Record<Rating, number>; majority?: string }) {
  const total = Object.values(t).reduce((a, b) => a + b, 0) || 1;
  const order: Rating[] = ["buy", "hold", "sell", "insufficient"];
  const text = order.filter((k) => t[k]).map((k) => `${t[k]} ${R_LABEL[k]}`).join(", ");
  return (
    <span className="flex items-center justify-end gap-2" aria-label={`Tally: ${text}`}>
      <span className="num w-14 text-right text-xs">{majority ? <span className={majority === "buy" ? "text-gain" : majority === "sell" ? "text-loss" : ""}>{R_LABEL[majority as Rating]}</span> : <span className="text-muted">Split</span>}</span>
      <span className="flex h-2 w-28 overflow-hidden rounded-full bg-sunken" aria-hidden>
        {order.map((k) => t[k] ? <span key={k} className={R_TONE[k]} style={{ width: `${(100 * t[k]) / total}%` }} /> : null)}
      </span>
      <span className="num w-28 text-xs text-muted">{t.buy}/{t.hold}/{t.sell}{t.insufficient ? ` · ${t.insufficient} no view` : ""}</span>
    </span>
  );
}

function Settings({ d, onChange }: { d: CouncilOverview; onChange: (x: CouncilOverview) => void }) {
  const save = async (b: { min_weight?: boolean; weekday?: number }) => onChange(await api.councilSettings(b));
  return (
    <div className="mt-5 flex flex-wrap items-center gap-4 text-sm">
      <Segmented size="sm" label="Which stocks" value={d.settings.min_weight ? "min" : "all"} onChange={(v) => save({ min_weight: v === "min" })}
        options={[{ value: "all", label: "All holdings" }, { value: "min", label: "Only holdings ≥ 2% of a portfolio" }]} />
      <label className="flex items-center gap-2 text-muted">Weekly run
        <select className="field h-8 w-auto py-0 text-sm" value={d.settings.weekday} onChange={(e) => save({ weekday: Number(e.target.value) })}>
          {DAYS.map((x, i) => <option key={x} value={i}>{x}</option>)}<option value={-1}>Off</option>
        </select>
      </label>
    </div>
  );
}

function StockCouncil({ ticker }: { ticker: string }) {
  const q = useData(() => api.councilStock(ticker), `council-${ticker}`);
  const s: CouncilStock | null = q.data;
  if (q.error) return <ErrorLine message={q.error.message} onRetry={q.reload} />;
  if (!s) return <Skel className="h-40 w-full" />;
  const v = s.verdict;
  if (!v) return <p className="text-sm text-muted">Not rated yet.</p>;
  return (
    <div className="space-y-6 text-sm">
      <p className="text-xs text-faint">{s.label} Rated {dateLong(v.rated_at)} at {v.anchor_price != null ? num(v.anchor_price, 2) : <Blank why="No price on the rating day." />} (SPY {v.anchor_spy != null ? num(v.anchor_spy, 2) : <Blank why="No SPY price on the rating day." />}).</p>
      <div className="flex flex-wrap items-center gap-4"><TallyBar t={v.tally} majority={v.majority} /><span>{v.consensus}{v.quality && ` · ${v.quality}`}</span>
        {v.crowded && <span className="text-loss">Crowded</span>}{v.change && <span>{v.change}</span>}</div>
      {v.weighted && <p className="text-muted">Weighted by track record: {Object.entries(v.weighted).map(([k, n]) => `${R_LABEL[k as Rating]} ${n}`).join(", ")} (the raw tally above is the default).</p>}
      {v.thesis && <div><p className="font-medium">Merged thesis</p><p className="mt-1 max-w-[70ch]">{v.thesis}</p></div>}
      {v.dissent && <div><p className="font-medium">Strongest dissent</p><p className="mt-1 max-w-[70ch]">{v.dissent}</p></div>}
      {v.contradictions.length > 0 && <div><p className="font-medium text-loss">Contradicts the data</p><ul className="mt-1 list-disc pl-5">{v.contradictions.map((c) => <li key={c}>{c}</li>)}</ul></div>}
      {v.devils_argument && <div><p className="font-medium">Devil's advocate</p><p className="mt-1 max-w-[70ch] text-muted">{v.devils_argument}</p></div>}
      <div>
        <p className="font-medium">Each model</p>
        <ul className="mt-2 divide-y divide-line">{s.models.map((m) => (
          <li key={m.label} className="py-3">
            <p><span className={clsx("font-semibold", m.final === "buy" ? "text-gain" : m.final === "sell" ? "text-loss" : "")}>{R_LABEL[m.final]}</span>
              <span className="text-muted"> · Model {m.label}{m.devil ? " (devil's advocate)" : ""} · {m.models[0]}{m.round1.conviction ? ` · conviction ${m.round1.conviction}/5` : ""}</span>
              {v.flipflops.includes(m.label) && <span className="text-xs text-loss"> · flip-flop</span>}</p>
            <p className="mt-1 max-w-[70ch]">{m.round1.thesis || <span className="text-faint">No thesis.</span>}</p>
            {m.round1.risk && <p className="mt-1 text-muted">Main risk: {m.round1.risk}</p>}
            {m.round2 && m.round2.rating !== m.round1.rating && <p className="mt-1">Changed from {R_LABEL[m.round1.rating]} in round 2: {m.round2.new_reason}</p>}
            <p className="mt-1 text-xs text-faint">{(m.round2?.tags?.length ? m.round2.tags : m.round1.tags).join(", ")}</p>
          </li>
        ))}</ul>
      </div>
      {s.pack && <div><p className="font-medium">The data pack it used</p><StockView s={s.pack} /></div>}
    </div>
  );
}

function Board() {
  const q = useData(api.councilBoard, "council-board");
  const rows = q.data?.rows ?? [];
  const da = q.data?.devils_advocate;
  return (
    <section className="mt-16" aria-label="Council track record">
      <h2 className="text-sm font-semibold">Track record vs SPY</h2>
      {rows.length === 0 ? <p className="mt-1 text-xs text-faint">Every rating is stored with the price and SPY level of its day, and scored after 3, 6 and 12 months (never on past dates). Results show here once the first ones are due.</p> : (
        <table className="mt-2 w-full max-w-[760px] text-xs">
          <thead><tr className="text-muted">{["Who", "Horizon", "Rating", "Scored", "Hit rate", "Avg vs SPY"].map((h, i) => <th key={h} className={clsx("border-b border-line py-1.5 font-medium", i > 2 ? "pl-3 text-right" : "text-left")}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((x, i) => (
              <tr key={i}><td className="border-b border-line py-1.5">{x.who}</td><td className="border-b border-line py-1.5">{x.horizon}</td><td className="border-b border-line py-1.5">{x.rating}</td>
                <td className="num border-b border-line py-1.5 pl-3 text-right">{x.n}</td><td className="num border-b border-line py-1.5 pl-3 text-right">{pct(x.hit_rate, 0, false)}</td>
                <td className="num border-b border-line py-1.5 pl-3 text-right"><Signed value={x.avg_excess}>{pct(x.avg_excess, 1)}</Signed></td></tr>
            ))}
            <tr><td className="border-b border-line py-1.5">Just SPY</td><td className="border-b border-line py-1.5" colSpan={4}>0 by definition</td><td className="num border-b border-line py-1.5 pl-3 text-right">0.0%</td></tr>
          </tbody>
        </table>
      )}
      {da && da.scored > 0 && <p className="mt-2 text-xs text-muted">Devil's advocate was right {da.right} of {da.scored} times ({pct(da.rate ?? 0, 0, false)}).</p>}
    </section>
  );
}
