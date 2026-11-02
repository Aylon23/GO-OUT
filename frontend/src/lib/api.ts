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
export interface PortfolioInfo { id: number; name: string; note: string; aliases: string; transactions: number }

export interface Portfolio {
  empty: boolean;
  base: Ccy;
  benchmarks: string[];
  warnings: string[];
  range: { code: string; start: string; end: string; first_date: string; tracking_since: string; short_history: boolean };
  summary: {
    cost_gain: number; cost_gain_pct: number | null; has_cost_basis: boolean; realized: number | null;
    value: number; invested: number; total_gain: number; range_gain: number; today_change: number; today_pct: number | null; today_date: string | null;
    twr: number; xirr: number | null; benchmarks: { symbol: string; twr: number | null }[]; start_value: number;
  };
  series: Series;
  shadow: { actual: number; rows: { symbol: string; value: number; diff: number; diff_pct: number | null }[] };
}
export interface HoldingRow {
  ticker: string; name: string; type: string; sector: string; currency: Ccy; quantity: number; avg_cost: number; price: number;
  value: number; weight: number; unrealized: number; unrealized_pct: number | null; range_return: number | null; contribution: number; closed: boolean;
  realized: number | null;
  price_status: "ok" | "stale" | "missing"; price_date: string | null; unrealized_native: number | null; opening: boolean;
  today: number | null; today_native: number | null; today_pct: number | null;
}
export interface Holdings {
  empty: boolean; base: Ccy; range: { code: string; start: string; end: string; tracking_since?: string }; lookback_basis?: boolean; total: number; cash: number;
  warnings?: string[]; today?: { change: number | null; pct: number | null; date: string | null };
  realized: number | null; realized_other: { ticker: string; amount: number }[];
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
  id: number; name: string; note: string; empty: boolean; value?: number; cash?: number; cost_gain?: number; cost_gain_pct?: number | null; realized?: number | null;
  today_pct?: number | null; today_change?: number | null; tracking_since?: string; holdings?: number;
  period_start?: string; short_history?: boolean; return?: number | null; bench?: Record<string, number | null>;
  since_return?: number | null; since_spy?: number | null; since_vs_spy?: number | null; trading_days?: number; spark?: (number | null)[];
  cost_gain_usd?: number | null; last_activity?: string;
}
export interface Insight { kind: "winner" | "loser" | "shared" | "stock" | "sector"; tone: "gain" | "loss" | "warn" | "info"; text: string; symbol?: string }
export interface Overview {
  base: Ccy; range: string; since: string | null; total_value: number; rows: OverviewRow[]; benchmarks: { symbol: string; twr: number | null }[];
  insights: Insight[]; min_trading_days: number;
  chart: { dates: string[]; perf: Record<string, (number | null)[]>; labels: Record<string, string> };
}
export interface HoldingImportRow {
  line: number; ticker: string; portfolio?: string | null; errors: Record<string, string>; notes: string[]; quantity?: number; avg_price?: number; currency?: Ccy;
  price?: number; price_date?: string; gain_pct?: number;
}
export interface HoldingImport {
  rows: HoldingImportRow[]; realized: { line: number; ticker: string; amount: number; note: string; portfolio: string | null }[];
  valid: number; invalid: number; committed: boolean; imported: number; realized_imported: number; portfolios: number; created: string[];
  groups: { name: string; exists: boolean; rows: number; valid: number; holding_now: number; realized_lines: number; realized_total: number }[];
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
  portfolio_matched: boolean; everyone: boolean; price_date?: string;
  choices: { ticker?: string[]; quantity?: number[] };
  targets: { portfolio_id: number; portfolio_name: string; quantity: number | null; held: number | null; skip: boolean; reason: string }[];
}
export interface OmniResult { saved: { portfolio: string; quantity: number }[]; failed: { portfolio: string; error: string }[]; rec_id: number | null }
export interface CallRow {
  id: number; ticker: string; action: "BUY" | "SELL"; date: string; price: number | null; thesis: string; target: number | null;
  closed_date: string | null; portfolios: string[]; return: number | null; spy: number | null; edge: number | null; worked: boolean | null;
  days: number | null; error: string | null; to_target?: number | null;
}
export interface ScoreBlock {
  count: number; scored: number; worked: number; hit_rate: number | null; avg_edge: number | null; median_edge: number | null;
  best: { id: number; ticker: string; edge: number } | null; worst: { id: number; ticker: string; edge: number } | null;
}
export interface Calls { base: Ccy; reference: string; rows: CallRow[]; scorecard: { all: ScoreBlock; buy: ScoreBlock; sell: ScoreBlock; avg_days_held: number | null } }
export interface TelegramStatus { configured: boolean; paired: boolean; pair_code: string; weekly_report: boolean; running: boolean; error: string; username: string }

export interface Settings {
  base_currency: Ccy; benchmarks: string[]; risk_free: number; theme: "light" | "dark" | "system"; auto_dividends: boolean;
  anthropic_key_set?: boolean; anthropic_key_hint?: string; anthropic_key?: string; allow_short_hedges?: boolean;
}

// ---- scenarios
export type ShockKind = "factor" | "stock";
export interface Shock { symbol: string; kind: ShockKind; move: number; label?: string; basis?: "text" | "typical" | "events" | "manual"; basis_text?: string; typical?: number | null; event?: string | null;
  split?: boolean; event_moves?: { id: string; move: number }[] }
export interface ScenarioParse {
  text: string; parser: "rules" | "ai"; label: string; themes: { id: string; label: string }[]; shocks: Shock[]; events: string[]; used_events: string[];
  horizon_weeks: number; notes: string[]; understood: boolean;
}
export interface ScenarioFactor { symbol: string; label: string; label_he: string; group: string }
export interface ScenarioEventInfo { id: string; name: string; he: string; start: string; end: string; tags: string[] }
export interface ScenarioLibrary { factors: ScenarioFactor[]; events: ScenarioEventInfo[]; ai: boolean }
export interface ImpactHolding {
  symbol: string; name: string; value: number; weight: number; move: number; move_normal: number; move_selloff: number; low: number; high: number; change: number;
  proxy: string | null; shocked: boolean; drivers?: { symbol: string; effect: number }[];
}
export interface ImpactBook {
  id: number | null; name: string; value: number; invested: number; impact: number; change: number; low: number; high: number;
  impact_normal: number; impact_selloff: number; change_normal: number; change_selloff: number;
  holdings?: ImpactHolding[]; losers?: ImpactHolding[]; gainers?: ImpactHolding[];
}
export type Idea = { amount: number; share_of_portfolio: number; offset: number; covers: number } & (
  | { kind: "trim"; symbol: string }
  | { kind: "rebalance"; symbols: string[]; target: number }
  | { kind: "cash" }
  | { kind: "add"; symbol: string; move: number }
  | { kind: "short"; symbol: string; inverse: string | null; risk_cut: number });
export interface Protection {
  status: "not_needed" | "ideas"; ideas: Idea[]; weak: boolean; hit?: number;
  basis: { kind: "model"; impact: number; change: number } | { kind: "event"; id: string; name: string; impact: number; change: number } | null;
}
export interface EventReplay {
  id: string; name: string; he: string; start: string; end: string; used: boolean;
  family: { impact: number; change: number; estimated_share: number };
  portfolios: { id: number; name: string; impact: number; change: number; estimated_share: number }[];
  factors: { symbol: string; label: string; move: number }[];
  worst: { symbol: string; move: number; estimated: boolean; change: number }[];
  best: { symbol: string; move: number; estimated: boolean; change: number }[];
}
export type Regime = "normal" | "selloff";
export interface PastExtreme { id: string; name: string; impact: number; change: number }
export interface ScenarioRun {
  empty: boolean; warnings: string[]; regime: Regime; horizon_weeks: number; base_currency: Ccy;
  shocks: (Shock & { z: number | null })[]; pinned: string | null;
  plausibility: { years: number | null; label: string; d2: number; seen: number | null; weeks: number | null };
  family: ImpactBook & { holdings: ImpactHolding[] }; portfolios: ImpactBook[]; portfolio_holdings: Record<string, ImpactHolding[]>;
  market: { symbol: string; label: string; label_he: string; group: string; move: number; low: number; high: number; shocked: boolean; pinned: boolean }[];
  reverse: { beyond: true; scale: number } | { beyond: false; target: number; scale: number; shocks: { symbol: string; move: number }[]; years: number | null; label: string } | null;
  protection: Record<string, Protection>; allow_shorts: boolean;
  history: { n: number; from: string; to: string; worst: PastExtreme; best: PastExtreme } | null;
  events: EventReplay[]; stress_weeks: number; weeks: number;
}
export interface SpecRange { low: number; mid: number; high: number; rationale?: string }
export interface SpecBranch {
  name: string; probability: number; cause: string; narrative: string;
  transmission?: { from: string; to: string; direction: "+" | "-"; explanation?: string }[];
  factor_moves: Record<string, SpecRange>; sector_overrides?: Record<string, SpecRange>;
  analogs?: { name: string; start: string; end: string; why_similar?: string }[];
  signposts?: { indicator: string; symbol?: string; condition: ">" | "<"; value: number; meaning: string }[];
}
export interface ScenarioSpec {
  spec_version: 1; kind: "scenario"; id: string; title: string; title_he?: string; created_at: string; author: string; question: string;
  horizon: string; summary: string; branches: SpecBranch[]; current_context?: { item: string; value: string; as_of: string }[];
  sources?: { title: string; url: string; date?: string }[]; confidence: "low" | "medium" | "high"; what_would_change_my_view?: string;
}
export interface SpecReport {
  errors: string[]; warnings: string[]; ok: boolean; checked_at: string;
  analogs: { branch: string; name: string; start: string; end: string; why_similar: string; no_data: string[];
    factors: { factor: string; claimed_mid: number; actual: number | null; contradicts: boolean }[] }[];
}
export interface SpecRow { id: string; title: string; title_he: string; horizon: string; branches: number; confidence: string; author: string; created_at: string; source: string; imported_at: string; warnings: number; spec?: ScenarioSpec; report?: SpecReport }
export interface ImpactStats { id: number | null; name: string; median: number; p5: number; p95: number; prob_loss: number;
  median_ils: number; p5_ils: number; p95_ils: number; mean: number; value_ils: number; mid?: number }
export interface ImpactHoldingRow { symbol: string; name: string; weight: number; value_ils: number; move: number; change_ils: number;
  why: { factor: string; effect: number }[]; own_news: boolean }
export interface ImpactResult {
  empty: boolean; spec: ScenarioSpec & { branches: (SpecBranch & { holding_overrides?: Record<string, SpecRange> })[] };
  n: number; seed: number; horizon: string; horizon_days: number; base_currency: Ccy; usd_ils: number;
  blended: ImpactStats[]; branches: { name: string; probability: number; cause: string; regime: "normal" | "selloff"; draws: number; books: ImpactStats[] }[];
  holdings: Record<string, ImpactHoldingRow[]>; histogram: { edges: number[]; counts: number[] };
  exposures: { family: Record<string, number> }; assumptions: string[]; warnings: string[];
  answer: { median_ils: number; median: number; p5_ils: number; p5: number; prob_loss: number };
  analogs?: (EventReplay & { branch: string; no_data?: boolean })[]; report?: SpecReport;
}
export type ImpactEdit = { branch: string; factor: string; mid: number };
export interface ActionOption {
  kind: "trim" | "rebalance" | "add" | "cash" | "nothing"; title: string; amount: number; share_of_portfolio: number;
  benefit_ils: number; cost_ils: number; other_ils: number; top_weight_before: number; top_weight_after: number;
  effective_before: number; effective_after: number; symbol?: string; symbols?: string[];
}
export interface PersonActions {
  id: number; name: string; value_ils: number; expected_ils: number; expected: number; median_ils: number; median: number; bad_ils: number; bad: number;
  drivers_holdings: { symbol: string; change_ils: number; move: number }[]; drivers_factors: { factor: string; effect: number }[];
  line: string; options: ActionOption[]; other_label: string; message: string;
}
export interface Actions { empty: boolean; title: string; horizon: string; people: PersonActions[]; rank_expected: number[]; rank_bad: number[]; other_label: string }
export interface ActionsBody { spec_id?: string; shocks?: { symbol: string; kind: ShockKind; move: number }[]; horizon_weeks?: number; used_events?: string[]; text?: string; regime?: "normal" | "selloff" | null; edits?: ImpactEdit[] }
export interface NewMoneyRow {
  symbol: string; score: number; robust_count: number; branches: number; robust: boolean; expected: number; worst_p5: number;
  expected_ils: number; bad_ils: number; by_scenario: Record<string, number>; low_confidence: boolean; short_history: boolean; weight_after?: number;
}
export interface NewMoney { amount: number; currency: Ccy; amount_ils: number; person: number | null; scenarios: { id: string; title: string; weight: number }[];
  rows: NewMoneyRow[]; top: NewMoneyRow[]; note: string | null; method: string }
export interface BookEntry { scenario_id: string; probability: number; title?: string }
export interface BookPositioning { id: number | null; name: string; fragility: number; fragility_ils: number;
  by_scenario: { scenario_id: string; title: string; median: number; share: number; median_ils: number }[];
  hurts_most: { title: string; median: number }[]; helps_most: { title: string; median: number }[] }
export interface BookResult { empty: boolean; reason?: string; answer: ImpactResult["answer"]; blended: ImpactStats[]; remainder: number; horizon: string;
  positioning: BookPositioning[]; most_fragile: { id: number; name: string; fragility: number; fragility_ils: number } | null; scenario_titles: Record<string, string>; n: number }
export interface Signpost { scenario: string; branch: string; indicator: string; symbol?: string; condition: ">" | "<"; value: number; meaning: string;
  current: number | null; as_of: string | null; distance: number | null; status: "triggered" | "near" | "far" | "no data" | "not tracked" }
export interface BookState { version: number | null; saved_at: string | null; entries: BookEntry[]; result: BookResult; signposts: Signpost[] }
export interface BookVersionRow { id: number; created_at: string; note: string; entries: BookEntry[] }
export interface BookCompare { a: { id: number; created_at: string; result: { answer: ImpactResult["answer"]; most_fragile: BookResult["most_fragile"] } | null };
  b: { id: number; created_at: string; result: { answer: ImpactResult["answer"]; most_fragile: BookResult["most_fragile"] } | null };
  changes: { title: string; from: number | null; to: number | null }[] }
export interface EarningsMoves { reaction_date: string; reaction: number; d5: number | null; d20: number | null; continued: boolean | null }
export interface EarningsCard {
  symbol: string; name: string; rank: number;
  data: { reported: string; results: { revenue?: number; revenue_est?: number; eps?: number; eps_est?: number; currency?: string }; guidance: string; guidance_detail?: string;
    key_points: string[]; market_reaction_summary?: string; related_symbols?: string[]; sources?: { title: string; url: string; date?: string }[];
    outlook: { days: string; weeks: string; bull_case?: string; bear_case?: string; confidence: string; days_direction?: string; weeks_direction?: string } };
  beat: { revenue: string | null; eps: string | null }; today: EarningsMoves | null; bigger_than_share: number | null;
  history: { events: (EarningsMoves & { date: string })[]; summary: { n: number; reaction_avg: number; reaction_abs_avg: number; reaction_min: number; reaction_max: number;
    d5_avg: number; d20_avg: number; d20_min: number; d20_max: number; continued_share: number | null } | null };
  impact: { holders: { id: number; name: string; value_ils: number; pnl_ils: number | null; weight: number }[]; exposure_ils: number; pnl_ils: number | null;
    related: { symbol: string; name: string; why: string; beta: number; expected_move: number; expected_ils: number; holders: string[] }[] };
  journal: { score_5d: string; score_20d: string; score: string; move_5d: number | null; move_20d: number | null };
}
export interface EarningsPayload { date: string | null; dates: string[]; companies: EarningsCard[]; last_trading_day: string }
export interface EarningsJournal { rows: { date: string; symbol: string; name: string; confidence: string; move_5d: number | null; move_20d: number | null; score_5d: string; score_20d: string; score: string; scored: boolean }[];
  accuracy: Record<string, { d5: { n: number; hit: number | null }; d20: { n: number; hit: number | null }; overall: { n: number; hit: number | null } }> }
export interface ExposureFactor { id: string; label: string; he: string; tip: string; when: string; shock: number }
export interface ExposureHolding {
  symbol: string; name: string; weight: number; value: number; betas: Record<string, number>; betas_usd: Record<string, number>;
  r2: number | null; r2_usd: number | null; resid_vol: number | null; weeks: number; short_history: boolean; low_confidence: boolean; sector: string;
}
export interface ExposureBook { id: number | null; name: string; value: number; cash_weight: number; betas: Record<string, number>; holdings: ExposureHolding[] }
export interface Exposures { as_of: string; window_weeks: number; currency: "ILS"; factors: ExposureFactor[]; sectors: Record<string, string>; low_r2: number; books: ExposureBook[] }
export interface ModelCheck {
  summary: { events: number; predictions: number; hit_rate: number | null; mae: number; mae_naive: number; in_range: number; family_mae: number; corr: number | null } | null;
  events: { id: string; name: string; start: string; end: string; story: string[]; holdings: number; family_predicted: number; family_actual: number; sp500: number; mae: number; mae_naive: number; hit_rate: number | null; in_range: number }[];
}
export interface SavedScenario { id: number; name: string; text: string; shocks: Shock[]; events: string[]; used_events: string[]; horizon_weeks: number; created_at: string }
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
  omnibox: (text: string, opts: { today: string; portfolio_ids?: number[]; choose?: Record<string, string | number> }, signal?: AbortSignal) =>
    request<OmniDraft>("POST", "/api/omnibox", { text, ...opts }, signal),
  omniCommit: (draft: OmniDraft, logCall: boolean) => request<OmniResult>("POST", "/api/omnibox/commit", { draft, log_call: logCall }),
  calls: () => request<Calls>("GET", "/api/calls"),
  createCall: (c: { ticker: string; action: string; date: string; thesis: string; target: number | null }) => request<{ id: number }>("POST", "/api/calls", c),
  updateCall: (id: number, c: { ticker: string; action: string; date: string; thesis: string; target: number | null }) => request<{ ok: boolean }>("PUT", `/api/calls/${id}`, c),
  deleteCall: (id: number) => request<{ ok: boolean }>("DELETE", `/api/calls/${id}`),
  report: (period: string, all = false) => request<{ text: string }>("GET", `/api/report?${qs({ period, all })}`),
  telegram: () => request<TelegramStatus>("GET", "/api/telegram"),
  saveTelegram: (b: { token?: string; weekly_report?: boolean }) => request<TelegramStatus>("PUT", "/api/telegram", b),
  newPairCode: () => request<TelegramStatus>("POST", "/api/telegram/pair"),
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
  updatePortfolio: (id: number, name: string, note: string, aliases = "") => request<PortfolioInfo>("PUT", `/api/portfolios/${id}`, { name, note, aliases }),
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
  exposures: (refresh = false) => request<Exposures>("GET", `/api/exposures${refresh ? "?refresh=true" : ""}`),
  specValidate: (text: string) => request<{ spec: ScenarioSpec; report: SpecReport }>("POST", "/api/specs/validate", { text }),
  specSave: (text: string) => request<SpecRow>("POST", "/api/specs", { text }),
  specs: () => request<SpecRow[]>("GET", "/api/specs"),
  spec: (id: string) => request<SpecRow>("GET", `/api/specs/${encodeURIComponent(id)}`),
  specDelete: (id: string) => request<{ ok: boolean }>("DELETE", `/api/specs/${encodeURIComponent(id)}`),
  inbox: () => request<{ path: string; failed: { file: string; error: string }[] }>("GET", "/api/inbox"),
  inboxCheck: () => request<{ file: string; ok: boolean; message: string }[]>("POST", "/api/inbox/check"),
  impactSpec: (id: string, b: { regime?: "normal" | "selloff" | null; edits?: ImpactEdit[] }, signal?: AbortSignal) =>
    request<ImpactResult>("POST", `/api/impact/spec/${encodeURIComponent(id)}`, b, signal),
  impactShocks: (b: { shocks: { symbol: string; kind: ShockKind; move: number }[]; horizon_weeks: number; used_events: string[]; text: string; regime?: "normal" | "selloff" | null; edits?: ImpactEdit[] }, signal?: AbortSignal) =>
    request<ImpactResult>("POST", "/api/impact/shocks", b, signal),
  actions: (b: ActionsBody) => request<Actions>("POST", "/api/actions", b),
  newMoney: (b: { amount: number; currency: Ccy; person?: number | null; use_book?: boolean }) => request<NewMoney>("POST", "/api/new-money", b),
  book: () => request<BookState>("GET", "/api/book"),
  saveBook: (entries: BookEntry[], note = "") => request<BookState>("PUT", "/api/book", { entries, note }),
  bookScenarios: () => request<{ id: string; title: string; kind: string; horizon: string; branches: number }[]>("GET", "/api/book/scenarios"),
  bookVersions: () => request<BookVersionRow[]>("GET", "/api/book/versions"),
  bookCompare: (a: number, b: number) => request<BookCompare>("GET", `/api/book/compare?a=${a}&b=${b}`),
  watchlist: () => request<string[]>("GET", "/api/watchlist"),
  watch: (symbol: string) => request<{ ok: boolean; symbol: string }>("POST", `/api/watchlist/${encodeURIComponent(symbol)}`),
  unwatch: (symbol: string) => request<{ ok: boolean }>("DELETE", `/api/watchlist/${encodeURIComponent(symbol)}`),
  earnings: (date?: string) => request<EarningsPayload>("GET", `/api/earnings${date ? `?date=${date}` : ""}`),
  earningsJournal: () => request<EarningsJournal>("GET", "/api/earnings/journal"),
  earningsImport: (text: string) => request<{ date: string; companies: number }>("POST", "/api/earnings/import", { text }),
  logoOverrides: () => request<string[]>("GET", "/api/logos/overrides"),
  uploadLogo: (symbol: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ symbol: string }>("POST", `/api/logo/${encodeURIComponent(symbol)}`, fd);
  },
  clearLogo: (symbol: string) => request<{ ok: boolean }>("DELETE", `/api/logo/${encodeURIComponent(symbol)}`),
  scenarioLibrary: () => request<ScenarioLibrary>("GET", "/api/scenario/library"),
  scenarioParse: (text: string, ai = true, signal?: AbortSignal) => request<ScenarioParse>("POST", "/api/scenario/parse", { text, ai }, signal),
  scenarioRun: (body: { shocks: { symbol: string; kind: ShockKind; move: number }[]; horizon_weeks: number; events: string[]; used_events: string[]; regime: Regime; portfolio_ids?: number[] }, signal?: AbortSignal) =>
    request<ScenarioRun>("POST", "/api/scenario/run", body, signal),
  scenarioCheck: () => request<ModelCheck>("GET", "/api/scenario/check"),
  scenarios: () => request<SavedScenario[]>("GET", "/api/scenarios"),
  saveScenario: (b: { name: string; text: string; shocks: { symbol: string; kind: ShockKind; move: number }[]; events: string[]; used_events: string[]; horizon_weeks: number }) =>
    request<SavedScenario>("POST", "/api/scenarios", b),
  deleteScenario: (id: number) => request<{ ok: boolean }>("DELETE", `/api/scenarios/${id}`),
};
