import { CaretDown, CaretUp } from "@phosphor-icons/react";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Avatar } from "../components/Avatar";
import { ChartSkeleton } from "../components/chart/SeriesChart";
import { RangeSelector } from "../components/RangeSelector";
import { EmptyState, ErrorLine, InfoTip, SidePanel, Signed, Skel } from "../components/ui";
import { api, type HoldingRow, type Holdings as H } from "../lib/api";
import { dateLong, money, monthYear, num, pct, pp } from "../lib/format";
import { useData, useMediaQuery } from "../lib/hooks";
import { useApp } from "../state";

type Key = "ticker" | "quantity" | "avg_cost" | "price" | "value" | "weight" | "unrealized" | "unrealized_pct" | "today" | "range_return" | "contribution" | "council";
type CouncilMap = Record<string, { action: string; why: string; majority: string; consensus: string }>;
const COLS: { key: Key; label: string; tip?: string; right?: boolean; w: string }[] = [
  { key: "ticker", label: "Symbol", w: "minmax(140px,1.6fr)" },
  { key: "quantity", label: "Qty", right: true, w: "64px" },
  { key: "avg_cost", label: "Avg cost", right: true, w: "88px", tip: "Average price paid per share you still hold, after splits." },
  { key: "price", label: "Price", right: true, w: "88px" },
  { key: "value", label: "Value", right: true, w: "100px" },
  { key: "weight", label: "Weight", right: true, w: "84px" },
  { key: "unrealized", label: "Unrealized", right: true, w: "112px", tip: "(Price now minus average cost) x shares. Shown in the stock's own currency for positions imported without dates." },
  { key: "today", label: "Today", right: true, w: "88px", tip: "Today's move: last close vs the close before, times your shares." },
  { key: "range_return", label: "Return", right: true, w: "72px", tip: "This holding's return over the selected range, while you held it." },
  { key: "contribution", label: "Contrib.", right: true, w: "80px", tip: "How many percentage points this holding added to or took from your portfolio's return over the range." },
  { key: "council", label: "Council", w: "150px", tip: "The AI council's rating turned into a suggestion for this portfolio (limit 15% per stock, est. tax 25% of the gain). AI opinion based on the data shown. Not advice." },
];

/** A table from 768px. Columns drop as the screen narrows so it never scrolls sideways:
 *  contribution below 1280px; quantity and average cost too below 1024px. */
const useCols = () => {
  const xl = useMediaQuery("(min-width: 1280px)");
  const lg = useMediaQuery("(min-width: 1024px)");
  if (xl) return COLS;
  if (lg) return COLS.filter((c) => c.key !== "contribution");
  return COLS.filter((c) => !["contribution", "quantity", "avg_cost", "council"].includes(c.key));
};

export default function Holdings() {
  const { range, portfolioId } = useApp();
  const council = useData(() => api.councilActions(portfolioId), `council-actions-${portfolioId}`);
  const councilMap: CouncilMap = council.data ?? {};
  const hasCouncil = Object.keys(councilMap).length > 0;
  const allCols = useCols();
  const cols = hasCouncil ? allCols : allCols.filter((c) => c.key !== "council");
  const key = JSON.stringify(range);
  const q = useData(() => api.holdings(range), `h-${key}`);
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: "value", dir: -1 });
  const [open, setOpen] = useState<HoldingRow | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const d = q.data;
  const rows = useMemo(() => {
    if (!d) return [];
    const r = d.rows.filter((x) => showClosed || !x.closed);
    return [...r].sort((a, b) => {
      const get = (x: HoldingRow) => (sort.key === "council" ? councilMap[x.ticker]?.action ?? "" : x[sort.key]);
      const av = get(a), bv = get(b);
      if (typeof av === "string" || typeof bv === "string") return String(av).localeCompare(String(bv)) * sort.dir;
      return (((av as number) ?? -Infinity) - ((bv as number) ?? -Infinity)) * sort.dir;
    });
  }, [d, sort, showClosed, councilMap]);

  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Holdings</h1>
        <RangeSelector trackingSince={d && !d.empty ? d.range.tracking_since : undefined} />
      </div>
      {q.error && !d ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <HoldingsSkeleton /> : d.empty ? (
        <EmptyState title="No holdings yet." body="Add a purchase on the Transactions page and it shows up here with its weight, gain and contribution." />
      ) : (
        <div className={clsx("transition-opacity duration", q.loading && "opacity-60")}>
          <Summary d={d} onOpen={setOpen} />
          {(d.warnings ?? []).map((w) => <p key={w} className="mt-6 text-sm text-muted">{w}</p>)}
          {d.lookback_basis && (
            <p className="mt-10 max-w-[75ch] text-xs text-faint">
              Return and contribution are simulated: today's shares held over the range, dividends reinvested. This portfolio's real history is shorter than the range.
            </p>
          )}
          <div className={clsx("text-sm", d.lookback_basis ? "mt-4" : "mt-12")}>
            <table className="hidden w-full table-fixed border-separate border-spacing-0 md:table" aria-label="Holdings">
              <colgroup>{cols.map((c) => <col key={c.key} style={{ width: c.key === "ticker" ? undefined : c.w }} />)}</colgroup>
              <thead><tr>
                {cols.map((c) => (
                  <th key={c.key} scope="col" aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                    className={clsx("sticky top-14 z-10 border-b border-line bg-bg py-2 text-xs font-medium text-muted", c.right ? "text-right" : "text-left", c.key !== "ticker" && "pl-2")}>
                    <span className={clsx("inline-flex items-center gap-1", c.right && "justify-end")}>
                      <button type="button" className="press flex items-center gap-1 rounded-sm hover:text-text" onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? (s.dir === 1 ? -1 : 1) : c.key === "ticker" ? 1 : -1 }))}>
                        {c.label}
                        {sort.key === c.key ? (sort.dir === 1 ? <CaretUp size={10} /> : <CaretDown size={10} />) : <span className="w-[10px]" />}
                      </button>
                      {c.tip && <InfoTip text={c.tip} />}
                    </span>
                  </th>
                ))}
              </tr></thead>
              <tbody>{rows.map((r) => <TableRow key={r.ticker} r={r} base={d.base} cols={cols} council={councilMap[r.ticker]} onOpen={() => setOpen(r)} />)}</tbody>
            </table>
            <div className="md:hidden" role="list" aria-label="Holdings">
              {rows.map((r) => <Row key={r.ticker} r={r} base={d.base} onOpen={() => setOpen(r)} />)}
            </div>
            <div className="flex items-center justify-between border-b border-line py-3 text-sm">
              <span className="text-muted">Cash</span>
              <span className="num">{money(d.cash, d.base, { decimals: 0 })}</span>
            </div>
            {d.realized != null && Math.abs(d.realized) >= 0.5 && (
              <div className="flex items-center justify-between border-b border-line py-3 text-sm">
                <span className="text-muted">
                  Realized gains
                  {d.realized_other.length > 0 && <span className="ml-2 text-xs text-faint" title={d.realized_other.map((x) => `${x.ticker} ${money(x.amount, d.base, { signed: true, decimals: 0 })}`).join(", ")}>
                    incl. {d.realized_other.length} closed position{d.realized_other.length === 1 ? "" : "s"} from your records</span>}
                </span>
                <Signed value={d.realized} className="font-medium">{money(d.realized, d.base, { signed: true, decimals: 0 })}</Signed>
              </div>
            )}
          </div>
          {d.rows.some((r) => r.closed) && (
            <button type="button" className="link mt-4 text-sm" onClick={() => setShowClosed((s) => !s)}>{showClosed ? "Hide closed positions" : "Show closed positions"}</button>
          )}
        </div>
      )}
      {open && <HoldingPanel row={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Row({ r, base, onOpen }: { r: HoldingRow; base: string; onOpen: () => void }) {
  return (
    <div role="listitem" tabIndex={0} onClick={onOpen} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      aria-label={`${r.ticker}, open details`}
      className="grid cursor-pointer grid-cols-2 gap-x-3 gap-y-1 border-b border-line py-3 transition-colors duration-fast hover:bg-hover">
      <div className="col-span-2 flex items-center gap-2.5">
        <Avatar ticker={r.ticker} size={26} />
        <span className="font-mono font-semibold">{r.ticker}</span>
        <span className="truncate text-muted">{r.name}</span>
        <span className="num ml-auto font-medium">{money(r.value, base, { decimals: 0 })}</span>
      </div>
      {r.price_status !== "ok" && <p className="col-span-2 text-xs text-faint">No price{r.price_date ? ` · last ${dateLong(r.price_date)}` : ""}</p>}
      <Kv k="Weight" v={pct(r.weight, 1, false)} />
      {r.closed ? <Kv k="Realized" v={<Signed value={r.realized}>{money(r.realized, base, { signed: true, decimals: 0 })}</Signed>} />
        : <Kv k="Unrealized" v={r.price_status === "missing" ? <span className="text-faint">–</span> : <Signed value={gainOf(r, base).v}>{money(gainOf(r, base).v, gainOf(r, base).c, { signed: true, decimals: 0 })} ({pct(r.unrealized_pct, 1)})</Signed>} />}
      <Kv k="Today" v={r.price_status !== "ok" ? <span className="text-faint">–</span> : <Signed value={r.today}>{money(r.today, base, { signed: true, decimals: 0 })} ({pct(r.today_pct, 2)})</Signed>} />
      <Kv k="Return" v={<Signed value={r.range_return}>{pct(r.range_return, 1)}</Signed>} />
      <Kv k="Contribution" v={<Signed value={r.contribution}>{pp(r.contribution, 2)}</Signed>} />
      <Kv k="Quantity" v={num(r.quantity, 4)} />
      <Kv k="Price, avg cost" v={r.price_status !== "ok" ? <span className="text-faint">No price</span> : `${money(r.price, r.currency, { decimals: 2 })}, ${money(r.avg_cost, r.currency, { decimals: 2 })}`} />
    </div>
  );
}

function TableRow({ r, base, cols, council, onOpen }: { r: HoldingRow; base: string; cols: typeof COLS; council?: CouncilMap[string]; onOpen: () => void }) {
  const showInName = council && !cols.some((c) => c.key === "council");
  const missing = r.price_status === "missing";
  const cell = (k: Key): React.ReactNode => {
    switch (k) {
      case "ticker": return (
        <span className="flex min-w-0 items-center gap-2.5">
          <Avatar ticker={r.ticker} size={26} />
          <span className="min-w-0">
            <span className="block font-mono font-semibold leading-5">{r.ticker}</span>
            <span className="block truncate text-xs text-muted">{r.name}</span>
            {showInName && <span className="block truncate text-xs" title={council.why}>Council: {council.action}</span>}
          </span>
        </span>);
      case "council": return council ? <span className="block text-left text-xs leading-4" title={council.why}>{council.action}</span> : <span className="text-faint">–</span>;
      case "quantity": return r.closed ? "–" : num(r.quantity, 4);
      case "avg_cost": return r.closed ? "–" : money(r.avg_cost, r.currency, { decimals: 2 });
      case "price": return r.price_status !== "ok"
        ? <span className="text-faint" title="No current price. The last known value is kept in the totals.">No price{r.price_date && <span className="block text-xs">last {dateLong(r.price_date)}</span>}</span>
        : money(r.price, r.currency, { decimals: 2 });
      case "value": return <span className="font-medium">{money(r.value, base, { decimals: 0 })}</span>;
      case "weight": return (
        <span className="flex items-center justify-end gap-2">
          <span className="hidden h-1 w-10 overflow-hidden rounded-full bg-sunken lg:block" aria-hidden><span className="block h-full rounded-full bg-muted" style={{ width: `${Math.min(100, (r.weight ?? 0) * 100)}%` }} /></span>
          <span>{pct(r.weight, 1, false)}</span>
        </span>);
      case "unrealized": return r.closed ? (
        <span className="leading-5"><Signed value={r.realized} className="block">{money(r.realized, base, { signed: true, decimals: 0 })}</Signed><span className="block text-xs text-faint">realized</span></span>
      ) : missing ? <span className="text-faint">–</span> : (
        <span className="leading-5">
          <Signed value={gainOf(r, base).v} className="block">{money(gainOf(r, base).v, gainOf(r, base).c, { signed: true, decimals: 0 })}</Signed>
          <Signed value={r.unrealized_pct} className="block text-xs">{pct(r.unrealized_pct, 1)}</Signed>
        </span>);
      case "today": return r.price_status !== "ok" ? <span className="text-faint">–</span> : (
        <span className="leading-5">
          <Signed value={r.today} className="block">{money(r.today, base, { signed: true, decimals: 0 })}</Signed>
          <Signed value={r.today_pct} className="block text-xs">{pct(r.today_pct, 2)}</Signed>
        </span>);
      case "range_return": return <Signed value={r.range_return}>{pct(r.range_return, 1)}</Signed>;
      case "contribution": return <Signed value={r.contribution}>{pp(r.contribution, 2)}</Signed>;
    }
  };
  return (
    <tr tabIndex={0} onClick={onOpen} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      aria-label={`${r.ticker}, open details`} className="cursor-pointer transition-colors duration-fast hover:bg-hover">
      {cols.map((c) => (
        <td key={c.key} className={clsx("num border-b border-line py-2.5 align-middle", c.right ? "text-right" : "text-left", c.key !== "ticker" && "pl-2")}>{cell(c.key)}</td>
      ))}
    </tr>
  );
}

/** Opening positions (imported, no purchase dates) show gain in the security's own currency. */
const gainOf = (r: HoldingRow, base: string) => (r.opening && r.unrealized_native != null ? { v: r.unrealized_native, c: r.currency } : { v: r.unrealized, c: base });

const Kv = ({ k, v }: { k: string; v: React.ReactNode }) => (
  <div className="flex justify-between gap-2 text-xs"><span className="text-faint">{k}</span><span className="num text-text">{v}</span></div>
);

function Summary({ d, onOpen }: { d: H; onOpen: (r: HoldingRow) => void }) {
  const by = (t: string) => d.rows.find((r) => r.ticker === t)!;
  const List = ({ title, items, none }: { title: string; items: string[]; none: string }) => (
    <div>
      <h2 className="text-sm font-medium text-muted">{title}</h2>
      <ol className="mt-2">
        {items.map((t) => { const r = by(t); return (
          <li key={t}>
            <button type="button" onClick={() => onOpen(r)} className="press flex w-full items-center gap-2.5 border-b border-line py-2 text-left text-sm hover:bg-hover">
              <Avatar ticker={t} size={22} /><span className="font-mono font-semibold">{t}</span>
              <Signed value={r.range_return} className="ml-auto">{pct(r.range_return, 1)}</Signed>
            </button>
          </li>); })}
        {items.length === 0 && <li className="border-b border-line py-2 text-sm text-faint">{none}</li>}
      </ol>
    </div>
  );
  const shades = ["var(--series-qqq)", "var(--series-spy)", "var(--series-4)", "var(--series-5)", "var(--muted)", "var(--line-strong)"];
  return (
    <div className="mt-10 grid gap-10 md:grid-cols-[1fr_1fr_1.4fr]">
      <List title="Best in this range" items={d.best} none="Nothing gained in this range." />
      <List title="Worst in this range" items={d.worst} none="Nothing lost in this range." />
      <div>
        <h2 className="text-sm font-medium text-muted">Sectors</h2>
        <div className="mt-3 flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full" role="img" aria-label={d.sectors.map((s) => `${s.sector} ${pct(s.weight, 0, false)}`).join(", ")}>
          {d.sectors.map((s, i) => <span key={s.sector} style={{ width: `${s.weight * 100}%`, background: s.sector === "Cash" ? "var(--sunken)" : shades[i % shades.length] }} />)}
        </div>
        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {d.sectors.map((s, i) => (
            <li key={s.sector} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: s.sector === "Cash" ? "var(--line-strong)" : shades[i % shades.length] }} />
              <span className="text-muted">{s.sector}</span><span className="num">{pct(s.weight, 0, false)}</span>
            </li>
          ))}
        </ul>
        {d.concentrated.length > 0 && (
          <p className="mt-4 text-sm text-muted">
            {d.concentrated.map((t, i) => <span key={t}>{i > 0 && " and "}<span className="font-mono font-semibold text-text">{t}</span> is {pct(by(t).weight, 0, false)}</span>)} of your portfolio. A single position above 25% moves your whole result.
          </p>
        )}
      </div>
    </div>
  );
}

function HoldingPanel({ row, onClose }: { row: HoldingRow; onClose: () => void }) {
  const { range } = useApp();
  const base = useBase();
  const r = range.range === "CUSTOM" ? "MAX" : range.range;
  const q = useData(() => api.holding(row.ticker, r), `hd-${row.ticker}-${r}`);
  const d = q.data;
  const markers = d?.markers ?? [];
  const priceAt = (date: string) => { const p = d?.prices.find((x) => x.date >= date); return p?.close; };
  return (
    <SidePanel open title={row.ticker} onClose={onClose} wide>
      <p className="-mt-2 text-sm text-muted">{row.name}</p>
      <div className="mt-5 grid grid-cols-3 gap-4 border-y border-line py-3 text-sm">
        <div><div className="text-xs text-faint">Value</div><div className="num font-medium">{money(row.value, base, { decimals: 0 })}</div></div>
        <div><div className="text-xs text-faint">Unrealized</div><Signed value={row.unrealized_pct} className="font-medium">{pct(row.unrealized_pct, 1)}</Signed></div>
        <div><div className="text-xs text-faint">Weight</div><div className="num font-medium">{pct(row.weight, 1, false)}</div></div>
      </div>
      <div className="mt-6">
        <div className="mb-2 flex items-center gap-4 text-xs text-muted">
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-accent" /> Buy</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full border border-text bg-surface" /> Sell</span>
          <span className="ml-auto text-faint">Price in {row.currency}, split-adjusted</span>
        </div>
        {q.error ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <ChartSkeleton height={220} /> : (
          <div className="fade-in" role="img" aria-label={`${row.ticker} price with your trades`}>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={d.prices} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis dataKey="date" tickFormatter={(x) => monthYear(x)} tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 12 }} minTickGap={48} />
                <YAxis width={56} tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 12 }} domain={["auto", "auto"]} tickFormatter={(v) => money(v, row.currency, { decimals: 0 })} />
                <Tooltip isAnimationActive={false} cursor={{ stroke: "var(--line-strong)" }} content={({ active, payload, label }) => active && payload?.length ? (
                  <div className="num rounded-sm bg-surface px-3 py-2 text-sm shadow-pop"><div className="text-xs text-muted">{dateLong(String(label))}</div>{money(payload[0].value as number, row.currency, { decimals: 2 })}</div>) : null} />
                <Line dataKey="close" stroke="var(--series-qqq)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                {markers.map((m, i) => {
                  const y = priceAt(m.date);
                  return y === undefined ? null : (
                    <ReferenceDot key={i} x={d.prices.find((x) => x.date >= m.date)?.date} y={y} r={4}
                      fill={m.type === "BUY" ? "var(--accent)" : "var(--surface)"} stroke={m.type === "BUY" ? "var(--surface)" : "var(--text)"} strokeWidth={1.5} />
                  );
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
      <h3 className="mt-8 text-sm font-medium">Your transactions</h3>
      <ul className="mt-2 text-sm">
        {!d ? Array.from({ length: 3 }).map((_, i) => <li key={i} className="border-b border-line py-2.5"><Skel className="h-4 w-full" /></li>) :
          d.transactions.map((t) => (
            <li key={t.id} className="grid grid-cols-[96px_1fr_auto] gap-3 border-b border-line py-2.5">
              <span className="num text-muted">{dateLong(t.date)}</span>
              <span>{t.type === "BUY" ? "Buy" : t.type === "SELL" ? "Sell" : "Dividend"} {t.type !== "DIVIDEND" && <span className="num">{num(t.quantity, 4)} @ {money(t.price, t.currency, { decimals: 2 })}</span>}</span>
              <span className="num text-right">{money(t.type === "DIVIDEND" ? t.price : t.quantity * t.price, t.currency, { decimals: 0 })}</span>
            </li>
          ))}
      </ul>
    </SidePanel>
  );
}

function useBase() {
  const { settings } = useApp();
  return settings?.base_currency ?? "USD";
}

function HoldingsSkeleton() {
  return (
    <div aria-hidden className="mt-10">
      <div className="grid gap-10 md:grid-cols-[1fr_1fr_1.4fr]">{[0, 1, 2].map((i) => <div key={i} className="space-y-3"><Skel className="h-4 w-32" /><Skel className="h-7 w-full" /><Skel className="h-7 w-full" /><Skel className="h-7 w-full" /></div>)}</div>
      <div className="mt-12 space-y-0">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="flex items-center gap-4 border-b border-line py-3"><Skel className="h-7 w-7" /><Skel className="h-4 w-28" /><div className="flex-1" /><Skel className="h-4 w-20" /><Skel className="h-4 w-20" /></div>)}</div>
    </div>
  );
}
