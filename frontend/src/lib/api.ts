export type Ccy = "USD" | "ILS";

export interface SymbolItem {
  symbol: string;
  name: string;
  exchange: string;
  type: string;
  currency: string;
  raw?: boolean;
}
export interface Quote { symbol: string; price: number; change_pct: number; date: string; currency: string; stale?: boolean }
export interface SearchResponse { results: SymbolItem[]; source: string; error?: string }

export type TxType = "BUY" | "SELL" | "DIVIDEND" | "DEPOSIT" | "WITHDRAWAL";
export interface Tx { id?: number; ticker: string; type: TxType; date: string; quantity: number; price: number; currency: Ccy; fees: number; note: string }

export interface Series {
  dates: string[];
  value: (number | null)[];
  invested: (number | null)[];
  perf: Record<string, (number | null)[]>;
  drawdown: Record<string, (number | null)[]>;
}
export interface PortfolioInfo { id: number; name: string; note: string; transactions: number }

export interface Portfolio {
  empty: boolean;
  base: Ccy;
  benchmarks: string[];
  warnings: string[];
  range: { code: string; start: string; end: string; first_date: string; tracking_since: string; short_history: boolean };
  summary: {
    cost_gain: number; cost_gain_pct: number | null; has_cost_basis: boolean;
    value: number; invested: number; total_gain: number; range_gain: number; today_change: number; today_pct: number;
    twr: number; xirr: number | null; benchmarks: { symbol: string; twr: number | null }[]; start_value: number;
  };
  series: Series;
  shadow: { actual: number; rows: { symbol: string; value: number; diff: number; diff_pct: number | null }[] };
}
export interface HoldingRow {
  ticker: string; name: string; type: string; sector: string; currency: Ccy; quantity: number; avg_cost: number; price: number;
  value: number; weight: number; unrealized: number; unrealized_pct: number | null; range_return: number | null; contribution: number; closed: boolean;
}
export interface Holdings {
  empty: boolean; base: Ccy; range: { code: string; start: string; end: string }; lookback_basis?: boolean; total: number; cash: number;
  rows: HoldingRow[]; best: string[]; worst: string[]; sectors: { sector: string; weight: number }[]; concentrated: string[];
}
export interface HoldingDetail {
  ticker: string; name: string; currency: Ccy; prices: { date: string; close: number }[];
  markers: { date: string; type: TxType; price: number; quantity: number }[]; transactions: Tx[];
}
export interface Stat {
  cagr: number | null; volatility: number | null; sharpe: number | null; beta: number | null; correlation: number | null;
  max_drawdown: { value: number; peak: string | null; trough: string | null; recovery: string | null };
  best_year: { year: number; value: number } | null; worst_year: { year: number; value: number } | null; total_return: number | null;
}
export interface Risk {
  empty: boolean; range: { code: string; start: string; end: string; tracking_since?: string; short_history?: boolean }; risk_free: number; beta_reference: string | null;
  columns: string[]; stats: Record<string, Stat>; years: ({ year: number } & Record<string, number | null>)[]; monthly: Record<string, number | null>;
}
export interface Lookback {
  empty: boolean; base: Ccy; range: { code: string; start: string; end: string }; clipped: boolean; first_common: string; holdings: number;
  summary: { return: number; benchmarks: { symbol: string; twr: number | null }[]; start_value: number; end_value: number };
  cost: { positions_value: number; cost: number; gain: number; gain_pct: number | null };
  columns: string[]; stats: Record<string, Stat>; years: ({ year: number } & Record<string, number | null>)[]; monthly: Record<string, number | null>;
  series: Series;
}
export interface OverviewRow {
  id: number; name: string; note: string; empty: boolean; value?: number; cash?: number; cost_gain?: number; cost_gain_pct?: number | null;
  today_pct?: number | null; tracking_since?: string; holdings?: number;
  period_start?: string; short_history?: boolean; return?: number | null; bench?: Record<string, number | null>;
}
export interface Overview {
  base: Ccy; range: string; since: string | null; total_value: number; rows: OverviewRow[]; benchmarks: { symbol: string; twr: number | null }[];
  chart: { dates: string[]; perf: Record<string, (number | null)[]>; labels: Record<string, string> };
}
export interface HoldingImportRow {
  line: number; ticker: string; portfolio?: string | null; errors: Record<string, string>; notes: string[]; quantity?: number; avg_price?: number; currency?: Ccy;
  price?: number; price_date?: string; gain_pct?: number;
}
export interface HoldingImport {
  rows: HoldingImportRow[]; valid: number; invalid: number; committed: boolean; imported: number; portfolios: number; created: string[];
  groups: { name: string; exists: boolean; rows: number; valid: number; holding_now: number }[];
}

export interface MetricRow {
  key: string; label: string; kind: "portfolio" | "symbol"; name?: string; note: string; beta: number | null;
  trailing_pe: number | null; forward_pe: number | null; dividend_yield: number | null;
  trailing_pe_coverage?: number; trailing_pe_missing?: string[]; forward_pe_coverage?: number;
}
export interface Metrics {
  base: Ccy; reference: string; window_days: number; range: { code: string; start: string | null; end: string };
  rows: MetricRow[]; warnings: string[]; rolling: { dates: string[]; series: Record<string, (number | null)[]> };
}

export interface OmniDraft {
  text: string; type: TxType; ticker: string; quantity: number | null; price: number | null; currency: Ccy; date: string;
  portfolio_id: number; portfolio_name: string; price_source?: "close" | "you"; missing: string[]; warnings: string[];
}

export interface Settings { base_currency: Ccy; benchmarks: string[]; risk_free: number; theme: "light" | "dark" | "system"; auto_dividends: boolean }
export interface Swap {
  base: Ccy; ticker: string; replacement: string; real_value: number; hypo_value: number; diff: number; diff_pct: number | null; since: string; trades: number;
  series: { dates: string[]; real: number[]; hypo: number[] };
}
export interface Custom {
  ok: boolean; errors?: { symbol: string; message: string; first_date?: string }[]; base: Ccy; range: { start: string; end: string };
  value: number; invested: number; twr: number; xirr: number | null; shadow: Record<string, number>; columns: string[]; stats: Record<string, Stat>; series: Series;
}
export interface PriceOnDate { symbol: string; date: string; requested_date: string; close: number; currency: Ccy; first_date: string }
export interface ImportResult { rows: { line: number; raw: Record<string, string>; errors: Record<string, string>; tx?: Tx }[]; valid: number; invalid: number; committed: boolean }

export class ApiError extends Error {
  status: number;
  fields: Record<string, string>;
  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

/** The portfolio every request is about. Set by the app state when the user switches. */
let currentPortfolio = 1;
export const setApiPortfolio = (id: number) => { currentPortfolio = id; };
export const portfolioParam = () => `portfolio=${currentPortfolio}`;

async function request<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  if (url.startsWith("/api/") && !/[?&]portfolio=/.test(url)) url += (url.includes("?") ? "&" : "?") + portfolioParam();
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      signal,
      headers: body instanceof FormData || body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the local server. Is it running? Start it with ./start.sh.");
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status}).`;
    let fields: Record<string, string> = {};
    try {
      const j = await res.json();
      const d = j.detail;
      if (typeof d === "string") msg = d;
      else if (d?.message) { msg = d.message; fields = d.fields ?? {}; }
      else if (Array.isArray(d)) msg = d.map((x: { msg: string }) => x.msg).join(" ");
    } catch { /* not json */ }
    throw new ApiError(res.status, msg, fields);
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const qs = (o: Record<string, string | number | boolean | undefined | null>) =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])).toString();

export interface RangeParams { range: string; start?: string; end?: string }

export const api = {
  search: (q: string, signal?: AbortSignal) => request<SearchResponse>("GET", `/api/search?${qs({ q })}`, undefined, signal),
  recent: () => request<SymbolItem[]>("GET", "/api/symbols/recent"),
  popular: () => request<SymbolItem[]>("GET", "/api/symbols/popular"),
  select: (s: SymbolItem) => request<SymbolItem>("POST", "/api/symbols/select", s),
  quote: (symbol: string) => request<Quote>("GET", `/api/quote/${encodeURIComponent(symbol)}`),
  quotes: (symbols: string[]) => request<Record<string, Quote>>("GET", `/api/quotes?${qs({ symbols: symbols.join(",") })}`),
  price: (symbol: string, date: string) => request<PriceOnDate>("GET", `/api/price/${encodeURIComponent(symbol)}?${qs({ date })}`),
  symbolChart: (symbol: string, range = "1Y") =>
    request<{ symbol: string; dates: string[]; series: Record<string, number[]>; price: number; currency: Ccy; first_date: string }>(
      "GET", `/api/symbol/${encodeURIComponent(symbol)}/chart?${qs({ range })}`),
  transactions: () => request<Tx[]>("GET", "/api/transactions"),
  createTx: (t: Tx) => request<Tx>("POST", "/api/transactions", t),
  createTxIn: (portfolioId: number, t: Tx) => request<Tx>("POST", `/api/transactions?portfolio=${portfolioId}`, t),
  omnibox: (text: string, signal?: AbortSignal) => request<OmniDraft>("POST", "/api/omnibox", { text }, signal),
  updateTx: (id: number, t: Tx) => request<Tx>("PUT", `/api/transactions/${id}`, t),
  deleteTx: (id: number) => request<{ ok: boolean }>("DELETE", `/api/transactions/${id}`),
  importCsv: (file: File, commit: boolean) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<ImportResult>("POST", `/api/transactions/import?${qs({ commit })}`, fd);
  },
  portfolio: (p: RangeParams) => request<Portfolio>("GET", `/api/portfolio?${qs({ ...p })}`),
  holdings: (p: RangeParams) => request<Holdings>("GET", `/api/holdings?${qs({ ...p })}`),
  holding: (t: string, range: string) => request<HoldingDetail>("GET", `/api/holdings/${encodeURIComponent(t)}?${qs({ range })}`),
  risk: (p: RangeParams) => request<Risk>("GET", `/api/risk?${qs({ ...p })}`),
  swap: (ticker: string, replacement: string) => request<Swap>("POST", "/api/whatif/swap", { ticker, replacement }),
  custom: (body: { holdings: { symbol: string; weight: number }[]; start: string; initial: number; monthly: number; rebalance: string }) =>
    request<Custom>("POST", "/api/whatif/custom", body),
  settings: () => request<Settings>("GET", "/api/settings"),
  saveSettings: (s: Partial<Settings>) => request<Settings>("PUT", "/api/settings", s),
  demo: () => request<{ loaded: number }>("POST", "/api/demo"),
  portfolios: () => request<PortfolioInfo[]>("GET", "/api/portfolios"),
  createPortfolio: (name: string, note = "") => request<PortfolioInfo>("POST", "/api/portfolios", { name, note }),
  updatePortfolio: (id: number, name: string, note: string) => request<PortfolioInfo>("PUT", `/api/portfolios/${id}`, { name, note }),
  deletePortfolio: (id: number) => request<{ ok: boolean }>("DELETE", `/api/portfolios/${id}`),
  metrics: (portfolios: number[], symbols: string[], range: string) =>
    request<Metrics>("GET", `/api/metrics?${qs({ portfolios: portfolios.join(","), symbols: symbols.join(","), range })}`),
  overview: (range: string) => request<Overview>("GET", `/api/overview?${qs({ range })}`),
  lookback: (p: RangeParams) => request<Lookback>("GET", `/api/lookback?${qs({ ...p })}`),
  importHoldings: (file: File, commit: boolean, asOf?: string, replace = false) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<HoldingImport>("POST", `/api/holdings/import?${qs({ commit, as_of: asOf, replace })}`, fd);
  },
  clearAll: () => request<{ ok: boolean }>("DELETE", "/api/transactions"),
};
