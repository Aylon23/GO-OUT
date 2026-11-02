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
import { useData } from "../lib/hooks";
import { useApp } from "../state";

type Key = "ticker" | "quantity" | "avg_cost" | "price" | "value" | "weight" | "unrealized" | "unrealized_pct" | "range_return" | "contribution";
const COLS: { key: Key; label: string; tip?: string; right?: boolean; w: string }[] = [
  { key: "ticker", label: "Symbol", w: "minmax(180px,1.6fr)" },
  { key: "quantity", label: "Qty", right: true, w: "80px" },
  { key: "avg_cost", label: "Avg cost", right: true, w: "96px", tip: "Average price paid per share you still hold, after splits." },
  { key: "price", label: "Price", right: true, w: "96px" },
  { key: "value", label: "Value", right: true, w: "112px" },
  { key: "weight", label: "Weight", right: true, w: "110px" },
  { key: "unrealized", label: "Unrealized", right: true, w: "128px", tip: "Gain or loss on shares you still hold, in your base currency, at today's price and FX." },
  { key: "range_return", label: "Return", right: true, w: "84px", tip: "This holding's return over the selected range, while you held it." },
  { key: "contribution", label: "Contrib.", right: true, w: "92px", tip: "How many percentage points this holding added to or took from your portfolio's return over the range." },
];

export default function Holdings() {
  const { range } = useApp();
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
      const av = a[sort.key], bv = b[sort.key];
      if (typeof av === "string" || typeof bv === "string") return String(av).localeCompare(String(bv)) * sort.dir;
      return (((av as number) ?? -Infinity) - ((bv as number) ?? -Infinity)) * sort.dir;
    });
  }, [d, sort, showClosed]);

  return (
    <div className="mx-auto max-w-content px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Holdings</h1>
        <RangeSelector />
      </div>
      {q.error && !d ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !d ? <HoldingsSkeleton /> : d.empty ? (
        <EmptyState title="No holdings yet." body="Add a purchase on the Transactions page and it shows up here with its weight, gain and contribution." />
      ) : (
        <div className={clsx("transition-opacity duration", q.loading && "opacity-60")}>
          <Summary d={d} onOpen={setOpen} />
          {d.lookback_basis && (
            <p className="mt-10 max-w-[75ch] text-xs text-faint">
              Return and contribution are simulated: today's shares held over the range, dividends reinvested. This portfolio's real history is shorter than the range.
            </p>
          )}
          <div className={clsx("text-sm", d.lookback_basis ? "mt-4" : "mt-12")} role="table" aria-label="Holdings">
            <div role="row" className="sticky top-14 z-10 hidden gap-3 border-b border-line bg-bg py-2 text-xs font-medium text-muted lg:grid" style={{ gridTemplateColumns: COLS.map((c) => c.w).join(" ") }}>
              {COLS.map((c) => (
                <span role="columnheader" key={c.key} aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"} className={clsx("flex items-center gap-1", c.right && "justify-end")}>
                  <button type="button" className="press flex items-center gap-1 rounded-sm hover:text-text" onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? (s.dir === 1 ? -1 : 1) : c.key === "ticker" ? 1 : -1 }))}>
                    {c.label}
                    {sort.key === c.key ? (sort.dir === 1 ? <CaretUp size={10} /> : <CaretDown size={10} />) : <span className="w-[10px]" />}
                  </button>
                  {c.tip && <InfoTip text={c.tip} />}
                </span>
              ))}
            </div>
            {rows.map((r) => <Row key={r.ticker} r={r} base={d.base} onOpen={() => setOpen(r)} />)}
            <div className="flex items-center justify-between border-b border-line py-3 text-sm">
              <span className="text-muted">Cash</span>
              <span className="num">{money(d.cash, d.base, { decimals: 0 })}</span>
            </div>
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
    <div role="row" tabIndex={0} onClick={onOpen} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      aria-label={`${r.ticker}, open details`}
      className="grid cursor-pointer grid-cols-2 gap-x-3 gap-y-1 border-b border-line py-3 transition-colors duration-fast hover:bg-hover lg:block lg:py-2.5">
      <div className="contents lg:hidden">
        <div className="col-span-2 flex items-center gap-2.5">
          <Avatar ticker={r.ticker} size={26} />
          <span className="font-mono font-semibold">{r.ticker}</span>
          <span className="truncate text-muted">{r.name}</span>
          <span className="num ml-auto font-medium">{money(r.value, base, { decimals: 0 })}</span>
        </div>
        <Kv k="Weight" v={pct(r.weight, 1, false)} />
        <Kv k="Unrealized" v={<Signed value={r.unrealized}>{money(r.unrealized, base, { signed: true, decimals: 0 })} ({pct(r.unrealized_pct, 1)})</Signed>} />
        <Kv k="Return" v={<Signed value={r.range_return}>{pct(r.range_return, 1)}</Signed>} />
        <Kv k="Contribution" v={<Signed value={r.contribution}>{pp(r.contribution, 2)}</Signed>} />
        <Kv k="Quantity" v={num(r.quantity, 4)} />
        <Kv k="Price, avg cost" v={`${money(r.price, r.currency, { decimals: 2 })}, ${money(r.avg_cost, r.currency, { decimals: 2 })}`} />
      </div>
      <div className="hidden lg:contents">
        <DesktopRow r={r} base={base} />
      </div>
    </div>
  );
}

function DesktopRow({ r, base }: { r: HoldingRow; base: string }) {
  return (
    <div className="grid items-center gap-3" style={{ gridTemplateColumns: COLS.map((c) => c.w).join(" ") }}>
      <span className="flex min-w-0 items-center gap-2.5">
        <Avatar ticker={r.ticker} size={26} />
        <span className="min-w-0">
          <span className="block font-mono font-semibold leading-5">{r.ticker}</span>
          <span className="block truncate text-xs text-muted">{r.name}</span>
        </span>
      </span>
      <span className="num text-right">{r.closed ? "–" : num(r.quantity, 4)}</span>
      <span className="num text-right">{r.closed ? "–" : money(r.avg_cost, r.currency, { decimals: 2 })}</span>
      <span className="num text-right">{money(r.price, r.currency, { decimals: 2 })}</span>
      <span className="num text-right font-medium">{money(r.value, base, { decimals: 0 })}</span>
      <span className="flex items-center justify-end gap-2">
        <span className="h-1 w-12 overflow-hidden rounded-full bg-sunken" aria-hidden><span className="block h-full rounded-full bg-muted" style={{ width: `${Math.min(100, (r.weight ?? 0) * 100)}%` }} /></span>
        <span className="num w-12 text-right">{pct(r.weight, 1, false)}</span>
      </span>
      <span className="text-right leading-5">
        <Signed value={r.unrealized} className="block">{money(r.unrealized, base, { signed: true, decimals: 0 })}</Signed>
        <Signed value={r.unrealized_pct} className="block text-xs">{pct(r.unrealized_pct, 1)}</Signed>
      </span>
      <Signed value={r.range_return} className="text-right">{pct(r.range_return, 1)}</Signed>
      <Signed value={r.contribution} className="text-right">{pp(r.contribution, 2)}</Signed>
    </div>
  );
}

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
  return settings?.base_currency ?? "ILS";
}

function HoldingsSkeleton() {
  return (
    <div aria-hidden className="mt-10">
      <div className="grid gap-10 md:grid-cols-[1fr_1fr_1.4fr]">{[0, 1, 2].map((i) => <div key={i} className="space-y-3"><Skel className="h-4 w-32" /><Skel className="h-7 w-full" /><Skel className="h-7 w-full" /><Skel className="h-7 w-full" /></div>)}</div>
      <div className="mt-12 space-y-0">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="flex items-center gap-4 border-b border-line py-3"><Skel className="h-7 w-7" /><Skel className="h-4 w-28" /><div className="flex-1" /><Skel className="h-4 w-20" /><Skel className="h-4 w-20" /></div>)}</div>
    </div>
  );
}
