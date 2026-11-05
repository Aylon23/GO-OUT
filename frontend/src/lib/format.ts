const SYM: Record<string, string> = { USD: "$", ILS: "₪" };
export const ccySymbol = (c: string) => SYM[c] ?? "";
const MINUS = "−";

export function money(v: number | null | undefined, ccy: string, opts: { decimals?: number; signed?: boolean } = {}): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  const decimals = opts.decimals ?? (Math.abs(v) >= 1000 ? 0 : 2);
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const sign = v < 0 ? MINUS : opts.signed && v > 0 ? "+" : "";
  return `${sign}${ccySymbol(ccy)}${s}`;
}

/** Compact money, only for the headline sentence: ₪1.2M, ₪41.2K. */
export function compactMoney(v: number, ccy: string): string {
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : "";
  const sym = ccySymbol(ccy);
  if (a >= 1e6) return `${sign}${sym}${(a / 1e6).toFixed(a >= 1e7 ? 1 : 2).replace(/\.0+$/, "")}M`;
  if (a >= 1e4) return `${sign}${sym}${(a / 1e3).toFixed(1).replace(/\.0$/, "")}K`;
  return `${sign}${sym}${Math.round(a).toLocaleString("en-US")}`;
}

/** The currency amounts are shown in (Settings: "Display amounts in"). Changing it never changes a return. */
let DISPLAY = "USD";
export const setDisplayCurrency = (c: string) => { DISPLAY = c || "USD"; };
export const displayCurrency = () => DISPLAY;
/** An amount in the display currency, whole units. */
export const amt = (v: number | null | undefined, signed = true) => money(v, DISPLAY, { decimals: 0, signed });

const CAUSES: Record<string, string> = { geopolitical: "Geopolitical", supply: "Supply shock", demand: "Demand shock", policy: "Policy", other: "" };
/** A scenario cause for people: "Supply shock", never the key; the catch-all "other" shows nothing. */
export const causeLabel = (c: string | null | undefined) => (c == null ? "" : CAUSES[c] ?? humanize(c));
/** Any internal key as words: "after_hours" becomes "After hours". */
export const humanize = (k: string) => (k ? (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ") : k);

/** A probability without false precision: "> 99%" and "< 1%" at the extremes. */
export function prob(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "–";
  if (v > 0.99) return "> 99%";
  if (v < 0.01) return "< 1%";
  return `${Math.round(v * 100)}%`;
}

export function pct(v: number | null | undefined, decimals = 2, signed = true): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  const s = Math.abs(v * 100).toFixed(decimals);
  const sign = v < 0 && Number(s) !== 0 ? MINUS : signed && v > 0 && Number(s) !== 0 ? "+" : "";
  return `${sign}${s}%`;
}

/** Percentage points */
export function pp(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  const s = Math.abs(v * 100).toFixed(decimals);
  return `${v < 0 && Number(s) !== 0 ? MINUS : v > 0 && Number(s) !== 0 ? "+" : ""}${s} pp`;
}

export function num(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  return v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: decimals });
}

export function ratio(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return "–";
  return v < 0 ? `${MINUS}${Math.abs(v).toFixed(2)}` : v.toFixed(2);
}

export const signClass = (v: number | null | undefined) =>
  v === null || v === undefined || !isFinite(v) || Math.abs(v) < 5e-5 ? "text-text" : v > 0 ? "text-gain" : "text-loss";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const parse = (d: string) => { const [y, m, dd] = d.split("-").map(Number); return { y, m, d: dd }; };
export const dateLong = (d: string) => { const p = parse(d); return `${p.d} ${MONTHS[p.m - 1]} ${p.y}`; };
export const dateShort = (d: string) => { const p = parse(d); return `${p.d} ${MONTHS[p.m - 1]}`; };
export const monthYear = (d: string) => { const p = parse(d); return `${MONTHS[p.m - 1]} ${p.y}`; };
export const monthName = (m: number) => MONTHS[m - 1];
export const fullMonthYear = (d: string) => {
  const p = parse(d);
  return `${["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][p.m - 1]} ${p.y}`;
};
export const today = () => {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
};

/** Start date of a range code, the same rule as the server (used to disable ranges). */
export function rangeStartDate(code: string, end = new Date()): string | null {
  const y = end.getFullYear(), m = end.getMonth(), d = end.getDate();
  const iso = (dt: Date) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
  const months: Record<string, number> = { "1M": 1, "3M": 3, "1Y": 12, "3Y": 36, "5Y": 60 };
  if (code === "YTD") return `${y - 1}-12-31`;
  if (months[code]) return iso(new Date(y, m - months[code], Math.min(d, 28)));
  return null;
}
