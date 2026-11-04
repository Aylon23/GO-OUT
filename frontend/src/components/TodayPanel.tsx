import clsx from "clsx";
import { useState } from "react";
import { api, type Portfolio } from "../lib/api";
import { dateLong, money, num, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { Avatar } from "./Avatar";
import { Signed, Skel } from "./ui";

/** Today's move, the way broker apps show it: the portfolio's P&L for the day, then every holding. */
export function TodayPanel({ p }: { p: Portfolio }) {
  const q = useData(() => api.holdings({ range: "1M" }), "today-holdings");
  const [all, setAll] = useState(false);
  const s = p.summary;
  const rows = (q.data?.rows ?? []).filter((r) => !r.closed).sort((a, b) => Math.abs(b.today ?? 0) - Math.abs(a.today ?? 0));
  const shown = all ? rows : rows.slice(0, 6);
  return (
    <section aria-labelledby="today-h" className="mt-10 border-y border-line py-5">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <h2 id="today-h" className="text-sm font-medium text-muted">Today{s.today_date && <span className="font-normal text-faint"> · {dateLong(s.today_date)}</span>}</h2>
        <Signed value={s.today_change} className="text-2xl font-semibold">{money(s.today_change, p.base, { signed: true, decimals: 0 })}</Signed>
        <Signed value={s.today_pct} className="text-lg">{pct(s.today_pct, 2)}</Signed>
      </div>
      {!q.data ? <div className="mt-4 space-y-2" aria-hidden>{[0, 1, 2].map((i) => <Skel key={i} className="h-5 w-full" />)}</div> : rows.length > 0 && (
        <table className="mt-4 w-full text-sm">
          <thead className="sr-only"><tr><th>Stock</th><th>Price (local)</th><th>Change</th><th>P&L today</th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.ticker} className="border-t border-line first:border-t-0">
                <td className="py-2"><span className="flex min-w-0 items-center gap-2.5"><Avatar ticker={r.ticker} size={22} />
                  <span className="font-mono font-semibold">{r.ticker}</span><span className="hidden truncate text-muted sm:inline">{r.name}</span></span></td>
                <td className="num py-2 text-right text-muted">{r.price_status !== "ok" ? <span className="text-faint">No price{r.price_date && <span className="block text-xs">last {dateLong(r.price_date)}</span>}</span> : <span title="Price in the stock's own currency">{money(r.price, r.currency, { decimals: 2 })}</span>}</td>
                <td className="py-2 text-right">{r.price_status !== "ok" ? <span className="text-faint">–</span> : <Signed value={r.today_pct}>{pct(r.today_pct, 2)}</Signed>}</td>
                <td className="w-28 py-2 text-right font-medium">{r.price_status !== "ok" ? <span className="text-faint">–</span> : <Signed value={r.today}>{money(r.today, p.base, { signed: true, decimals: 0 })}</Signed>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rows.length > 6 && (
        <button type="button" className={clsx("link mt-2 text-sm")} onClick={() => setAll((a) => !a)}>
          {all ? "Show fewer" : `Show all ${num(rows.length, 0)} holdings`}
        </button>
      )}
    </section>
  );
}
