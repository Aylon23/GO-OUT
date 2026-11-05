import clsx from "clsx";
import { useEffect, useState } from "react";
import { api, type MarketStatus } from "../lib/api";

let shared: MarketStatus | null = null;
const subs = new Set<(m: MarketStatus) => void>();
let timer: number | undefined;

function poll() {
  api.marketStatus().then((m) => { shared = m; subs.forEach((f) => f(m)); }).catch(() => {});
}

/** The US market state, refreshed every minute and shared by every component that asks. */
export function useMarket(): MarketStatus | null {
  const [m, setM] = useState(shared);
  useEffect(() => {
    subs.add(setM);
    if (timer === undefined) { poll(); timer = window.setInterval(poll, 60_000); }
    return () => { subs.delete(setM); };
  }, []);
  return m;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "13:40", or "Tue 23:00" when the time is not from today (local time). */
export function asOfText(iso: string): string {
  const d = new Date(iso);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toDateString() === new Date().toDateString() ? hm : `${DAYS[d.getDay()]} ${hm}`;
}

/** Header chip like a trading terminal: Pre-market, Open, After hours or Closed, with the time of the last price. */
export function MarketChip() {
  const m = useMarket();
  if (!m) return null;
  const open = m.state === "open";
  return (
    <span className="flex h-8 shrink-0 items-center gap-1.5 rounded-sm px-1 text-xs text-muted" role="status"
      title={`US market: ${m.label}. Prices as of ${asOfText(m.as_of)}, your time. Pre-market and after-hours quotes are never counted in returns.`}>
      <span aria-hidden className={clsx("h-1.5 w-1.5 rounded-full", open ? "bg-text" : "border border-faint")} />
      <span className="font-medium text-text">{m.label}</span>
      <span className="hidden text-faint 2xl:inline">as of {asOfText(m.as_of)}</span>
    </span>
  );
}

/** "Today" while the US session runs or just ended, otherwise "Last session". */
export function DayWord() {
  return <>{useMarket()?.day_word ?? "Today"}</>;
}
