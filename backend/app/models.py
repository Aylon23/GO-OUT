"""Database tables.

Conventions for Transaction:
- BUY / SELL: `quantity` shares at `price` per share, in `currency`. `fees` in the same currency.
- DIVIDEND: `ticker` set, `price` is the total cash received, `quantity` is 0.
- DEPOSIT / WITHDRAWAL: `ticker` empty, `price` is the cash amount, `quantity` is 0.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class TxType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FEE = "FEE"  # account or custody fee: lowers the return, not money taken out


CASH_TYPES = {TxType.DEPOSIT, TxType.WITHDRAWAL}
TRADE_TYPES = {TxType.BUY, TxType.SELL}


class Portfolio(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    note: str = ""
    aliases: str = ""  # other names for the command box and the bot, comma separated ("קרן, keren")
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    portfolio_id: int = Field(default=1, index=True)
    ticker: str = Field(default="", index=True)
    type: TxType
    date: dt.date = Field(index=True)
    quantity: float = 0.0
    price: float = 0.0
    currency: str = "USD"
    fees: float = 0.0
    note: str = ""
    # Average cost per share when a position was imported from a holdings list (no purchase
    # history). The BUY itself is recorded at that day's close so performance starts from
    # market value; this field keeps the real cost basis for gain/loss.
    cost_price: Optional[float] = None
    rec_id: Optional[int] = Field(default=None, index=True)  # the recommendation this trade followed
    origin: str = ""  # "opening" (holdings list, average cost), "history" (imported history), "" (entered here)
    source_hash: str = Field(default="", index=True)  # history imports: identical rows are never imported twice
    batch_id: Optional[int] = Field(default=None, index=True)  # the history import that added it

    @property
    def amount(self) -> float:
        """Cash amount of the transaction in its own currency (before fees)."""
        if self.type in TRADE_TYPES:
            return self.quantity * self.price
        return self.price


class PriceCache(SQLModel, table=True):
    """One row per ticker per trading day. Values are already in major units (agorot / 100 for .TA)
    and on the current split basis. `dividend` is per share, same basis."""

    ticker: str = Field(primary_key=True)
    date: dt.date = Field(primary_key=True)
    close: float
    adj_close: float
    dividend: float = 0.0
    currency: str = "USD"


class PriceCoverage(SQLModel, table=True):
    """Which date range is cached for a ticker, so we only ever fetch what is missing."""

    ticker: str = Field(primary_key=True)
    first_date: dt.date
    last_date: dt.date
    checked_at: dt.datetime


class SplitEvent(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    date: dt.date = Field(primary_key=True)
    ratio: float  # 4.0 means 4-for-1


class SymbolMeta(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    name: str = ""
    exchange: str = ""
    type: str = ""  # Stock / ETF / Index / Fund
    sector: str = ""
    country: str = ""
    currency: str = ""
    info_fetched: bool = False


class Recommendation(SQLModel, table=True):
    """A call you made ("buy AVGO", "sell TSLA"), followed by one or more portfolios."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    action: str = "BUY"  # BUY or SELL
    date: dt.date
    price: Optional[float] = None  # close on that date, filled in when created
    thesis: str = ""
    target: Optional[float] = None
    closed_date: Optional[dt.date] = None  # a BUY call ends when you call SELL on the same ticker
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    status: str = "approved"  # approved | suggested (inferred from trades, not yet confirmed) | dismissed
    source: str = ""  # "" = made here; "inferred" = from trades of 2+ portfolios close together
    people: str = ""  # inferred calls: JSON list of portfolio ids that traded


class RealizedEntry(SQLModel, table=True):
    """Profit or loss already taken, from records without full trade history (e.g. a spreadsheet)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    portfolio_id: int = Field(index=True)
    ticker: str = ""
    amount: float = 0.0
    currency: str = "USD"
    date: Optional[dt.date] = None
    note: str = ""
    archived_batch: Optional[int] = None  # set when a history import's sells replace this realized line



class Fundamentals(SQLModel, table=True):
    """Valuation figures from Yahoo, refreshed at most once a day."""

    ticker: str = Field(primary_key=True)
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    beta: Optional[float] = None
    dividend_yield: Optional[float] = None
    fetched_at: dt.datetime


class RecentSymbol(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    selected_at: dt.datetime


class Settings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    base_currency: str = "USD"
    benchmarks: str = "SPY,QQQ"  # comma separated
    risk_free: float = 0.04
    theme: str = "system"
    auto_dividends: bool = True
    telegram_token: str = ""
    telegram_chat_id: str = ""  # the only chat the bot listens to, set by pairing
    telegram_pair_code: str = ""
    weekly_report: bool = True
    weekly_report_sent: str = ""  # date of the last Sunday summary sent
    allow_short_hedges: bool = False
    inbox_dir: str = ""
    memo_language: str = "en"  # he | en (memo and periodic reports)
    report_last_weekly: str = ""  # period end of the last weekly reports generated
    report_last_monthly: str = ""
    report_auto_send: bool = True  # send new reports to my Telegram chat
    band_scale: float = 1.0  # widens scenario ranges when past outcomes fell outside them too often
    band_note: str = ""  # folder watched for researched files; empty = ./inbox next to the app  # protection ideas may include shorts and inverse funds
    anthropic_key: str = ""  # optional: lets Claude read scenarios; the free local rules work without it
    aliases_backfilled: bool = False  # nicknames generated once for portfolios made before nicknames existed
    backup_dir_extra: str = ""  # optional second backup folder (e.g. a synced Google Drive folder)
    ai_free_only: bool = True  # OpenRouter: refuse any model id that is not a :free variant
    council_min_weight: bool = False  # council: only holdings of at least 2% of some portfolio (plus the watchlist)
    council_weekday: int = 6  # weekly council run: 0 = Monday … 6 = Sunday; -1 = off
    ai_roster: str = ""  # JSON list of {role, models: [primary, fallback, fallback]}; empty = defaults
    ai_budget_usd: float = 10.0  # monthly ceiling for paid models (the analyst panel); spending stops at it
    ai_panel: str = ""  # JSON list of {key, model}: a model id set by hand for a panel seat; empty = newest flagship

    def benchmark_list(self) -> list[str]:
        return [b.strip().upper() for b in self.benchmarks.split(",") if b.strip()]


class Scenario(SQLModel, table=True):
    """A saved stress-test scenario: the sentence and the (edited) shocks."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    text: str = ""
    shocks: str = "[]"  # JSON list of {symbol, kind, move}
    events: str = "[]"  # JSON list of event ids shown
    used_events: str = "[]"  # JSON list of event ids the sizes came from
    horizon_weeks: float = 1.0
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class BookVersion(SQLModel, table=True):
    """One saved state of the scenario book: which scenarios, with my probability for each."""

    id: Optional[int] = Field(default=None, primary_key=True)
    entries: str = "[]"  # JSON: [{scenario_id, title, probability}]
    note: str = ""
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class EarningsDates(SQLModel, table=True):
    """Cached report dates per symbol (refreshed weekly)."""

    symbol: str = Field(primary_key=True)
    dates: str = "[]"  # JSON: [[YYYY-MM-DD, timing], ...]
    fetched_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class EarningsItem(SQLModel, table=True):
    """One company from an imported earnings brief, with its prediction-journal scores."""

    id: Optional[int] = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)  # the trading day reported
    symbol: str = Field(index=True)
    data: str = "{}"  # the company entry as imported
    confidence: str = ""
    days_direction: str = ""  # up | down | flat | "" (not scored)
    weeks_direction: str = ""
    base_close: Optional[float] = None  # close of the reaction day, the journal's starting point
    base_date: Optional[dt.date] = None
    move_5d: Optional[float] = None
    move_20d: Optional[float] = None
    score_5d: str = ""  # hit | miss | mixed
    score_20d: str = ""
    score: str = ""  # overall
    imported_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class ScenarioPrediction(SQLModel, table=True):
    """What the engine predicted for an imported scenario, scored once its horizon has passed."""

    id: Optional[int] = Field(default=None, primary_key=True)
    spec_id: str = Field(index=True)
    title: str = ""
    start: dt.date
    end: dt.date
    horizon: str = "1m"
    predicted: str = "{}"  # JSON: factor mids, family median/p5/p95, holdings snapshot
    actual: str = "{}"  # JSON once scored
    abs_error: Optional[float] = None
    inside: Optional[bool] = None
    scored_at: Optional[dt.datetime] = None


class PersonReport(SQLModel, table=True):
    """A generated weekly or monthly report (a person's, or the family summary), with its files."""

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = "weekly"  # weekly | monthly
    portfolio_id: Optional[int] = None  # None = family summary
    name: str = ""
    period_start: dt.date
    period_end: dt.date
    lang: str = "he"
    png: str = ""  # file names in data/reports
    pdf: str = ""
    data: str = "{}"
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    sent_at: Optional[dt.datetime] = None


class Watch(SQLModel, table=True):
    """A symbol on the watchlist (candidates for new money)."""

    symbol: str = Field(primary_key=True)
    added_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class ResearchedSpec(SQLModel, table=True):
    """A Scenario Spec researched outside the app and imported (docs/scenario-spec.schema.json)."""

    id: str = Field(primary_key=True)
    title: str = ""
    spec: str = "{}"  # the spec as imported, never edited
    report: str = "{}"  # the validation report at import
    source: str = "paste"  # paste | inbox
    imported_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class FactorExposure(SQLModel, table=True):
    """A holding's factor betas (see factors.py), in its own trading currency (view "local")."""

    symbol: str = Field(primary_key=True)
    view: str = Field(primary_key=True)
    betas: str = "{}"
    r2: Optional[float] = None
    resid_vol: Optional[float] = None  # annualized
    weeks: int = 0
    short_history: bool = False
    sector: str = ""  # sector ETF used, if any
    as_of: dt.date
    estimated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


from .market.funds import FundSectors  # noqa: E402,F401  (registers the table)
from .logos import LogoCache  # noqa: E402,F401  (registers the table)


class AiCall(SQLModel, table=True):
    """One logical AI call (with its retry and JSON repair, if any)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc), index=True)
    role: str = ""
    models: str = "[]"  # JSON: the requested chain (primary + fallbacks)
    model_used: str = ""  # what OpenRouter actually served
    status: str = ""  # ok | failed | refused
    requests: int = 0  # HTTP requests sent for this call (each counts against the daily free quota)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    payload_hash: str = ""  # sha256 of the first request body
    job_id: Optional[int] = None
    step_key: str = ""
    error: str = ""
    paid: bool = False  # a paid model: counted against the monthly budget, not the free daily quota
    cost: float = 0.0  # USD, as reported by OpenRouter for this call


class AiQuota(SQLModel, table=True):
    """Free-model daily requests as last read from GET /api/v1/key, plus requests sent since then."""

    id: int = Field(default=1, primary_key=True)
    day: str = ""  # UTC date the figures belong to
    limit: int = 0
    used: int = 0
    remaining: int = 0
    sent_since: int = 0  # requests this app sent after the last read
    checked_at: Optional[dt.datetime] = None


class AiJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = ""
    title: str = ""
    status: str = "queued"  # queued | running | waiting_quota | done | failed
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    resume_after: Optional[dt.datetime] = None
    note: str = ""


class AiJobStep(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    idx: int = 0
    key: str = ""
    role: str = ""
    request: str = "{}"  # JSON: {messages, schema_name, schema, max_tokens}
    status: str = "pending"  # pending | running | done | failed
    result: str = ""
    error: str = ""
    call_id: Optional[int] = None
    claimed_by: str = ""  # host:pid:boot of the process sending it (so two processes never send the same step)
    claimed_at: Optional[dt.datetime] = None


class StockPackCache(SQLModel, table=True):
    """Per-stock data pack and raw fundamentals, built at most once a day."""

    ticker: str = Field(primary_key=True)
    kind: str = Field(primary_key=True)  # "raw" (provider fundamentals) | "pack"
    day: str = ""
    data: str = "{}"


class AuditEntry(SQLModel, table=True):
    """Every write the app makes on the user's behalf, with what's needed to undo it."""

    id: Optional[int] = Field(default=None, primary_key=True)
    at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc), index=True)
    action: str = ""
    summary: str = ""
    detail: str = "{}"  # JSON
    undo: str = ""  # JSON describing how to undo; empty = nothing to undo
    undone_at: Optional[dt.datetime] = None


class ActionItem(SQLModel, table=True):
    """A suggested decision for the owner. Only a suggestion: nothing is recorded until confirmed."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc), index=True)
    source: str = ""  # council | debate | ...
    source_ref: str = ""  # e.g. council run id, debate id
    portfolio_id: Optional[int] = None
    symbol: str = ""
    what: str = ""
    why: str = ""
    suggested: str = ""
    impact_amt: float = 0.0  # dollar size used for ranking
    status: str = "open"  # open | done | snoozed | dismissed
    snooze_until: Optional[dt.date] = None
    reason: str = ""


class Debate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    text: str = ""
    horizon: str = "1m"
    cause: str = ""
    inputs: str = "{}"  # JSON: conversions, parsed shocks
    labels: str = "{}"  # JSON: label -> {role, models}
    devil: str = ""  # label of the devil's advocate
    pack: str = "{}"  # JSON context pack, identical in every request of the debate
    phase: str = "round1"  # round1 | round2 | judge | done | failed
    jobs: str = "{}"  # JSON: phase -> job id
    spec_id: str = ""  # the merged spec (saved as a researched spec)
    result: str = "{}"  # JSON: agreements, disputes, headline numbers, top hurt/helped, validation
    note: str = ""


class DebateSpec(SQLModel, table=True):
    """Round-1 spec of each model and the judge spec, kept for scoring when the horizon ends."""

    id: Optional[int] = Field(default=None, primary_key=True)
    debate_id: int = Field(index=True)
    kind: str = ""  # round1 | judge
    label: str = ""
    model: str = ""
    spec: str = "{}"
    start: dt.date = Field(default_factory=dt.date.today)
    horizon: str = "1m"
    scored_at: Optional[dt.date] = None
    direction_hits: int = 0
    direction_n: int = 0
    abs_error: Optional[float] = None
    in_range: int = 0
    range_n: int = 0


class CouncilRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    kind: str = "full"  # full (scheduled or on demand) | stock
    status: str = "preparing"  # preparing | running | done | failed
    universe: str = "[]"  # JSON tickers
    labels: str = "{}"  # JSON: label -> {role, models}
    devil: str = ""
    batches: str = "[]"  # JSON: [{tickers, phase, jobs: {round1, round2, judge}}]
    note: str = ""


class CouncilRating(SQLModel, table=True):
    """One model's rating of one stock in one round."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    ticker: str = Field(index=True)
    round: int = 1
    label: str = ""
    model: str = ""  # the model id requested first (the served one is in the call log)
    rating: str = ""  # buy | hold | sell | insufficient
    conviction: Optional[int] = None
    thesis: str = ""
    risk: str = ""
    change_mind: str = ""
    tags: str = "[]"
    new_reason: str = ""
    devils_argument: str = ""
    rated_at: dt.date = Field(default_factory=dt.date.today)
    anchor_price: Optional[float] = None
    anchor_spy: Optional[float] = None


class CouncilVerdict(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    ticker: str = Field(index=True)
    rated_at: dt.date = Field(default_factory=dt.date.today)
    tally: str = "{}"  # JSON raw counts
    weighted: str = ""  # JSON weighted tally (only with enough track record)
    consensus: str = ""  # unanimous | strong | split | insufficient
    majority: str = ""  # buy | hold | sell | "" (split or no votes)
    quality: str = ""  # diverse | single reason | ""
    crowded: bool = False
    thesis: str = ""
    dissent: str = ""
    contradictions: str = "[]"
    flipflops: str = "[]"  # JSON labels flagged
    change: str = ""  # e.g. "Hold → Sell, 3/5"
    conviction: Optional[float] = None
    upside: Optional[float] = None
    devils_argument: str = ""
    anchor_price: Optional[float] = None
    anchor_spy: Optional[float] = None


class ImportBatch(SQLModel, table=True):
    """One history import for one portfolio."""

    id: Optional[int] = Field(default=None, primary_key=True)
    portfolio_id: int = Field(index=True)
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    filename: str = ""
    status: str = "staged"  # staged (waiting for reconciliation) | applied | undone
    rows_total: int = 0
    rows_new: int = 0
    rows_duplicate: int = 0
    rows_error: int = 0
    first_date: Optional[dt.date] = None
    applied_at: Optional[dt.datetime] = None
    decisions: str = "{}"  # JSON symbol -> "history" | "opening"


class HistoryRow(SQLModel, table=True):
    """A validated row of a history import, kept until (and after) it is applied."""

    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: int = Field(index=True)
    idx: int = 0
    date: dt.date
    type: str = ""
    symbol: str = ""
    quantity: float = 0.0
    price: float = 0.0
    currency: str = "USD"
    fees: float = 0.0
    note: str = ""
    source_line: str = ""
    hash: str = Field(default="", index=True)
    status: str = "staged"  # staged | applied | kept_out (opening position kept) | info (SPLIT) | undone


class ArchivedTransaction(SQLModel, table=True):
    """A transaction taken out of the books (replaced by a history import, or an undone import). Never deleted."""

    id: Optional[int] = Field(default=None, primary_key=True)
    original_id: Optional[int] = None
    portfolio_id: int = Field(index=True)
    ticker: str = ""
    type: str = ""
    date: dt.date
    quantity: float = 0.0
    price: float = 0.0
    currency: str = "USD"
    fees: float = 0.0
    note: str = ""
    cost_price: Optional[float] = None
    rec_id: Optional[int] = None
    origin: str = ""
    source_hash: str = ""
    original_batch_id: Optional[int] = None
    archived_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    archived_by_batch: Optional[int] = Field(default=None, index=True)
    reason: str = ""
    restored: bool = False


class PanelRun(SQLModel, table=True):
    """One analyst-panel run: each model's own method and a move for every holding, next to the engine."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    text: str = ""
    horizon: str = "1m"
    web: bool = True  # live web search on for every model
    status: str = "running"  # running | done | failed
    progress: str = "{}"  # JSON: {pct, step, models: {key: waiting|working|done|failed}}
    seats: str = "[]"  # JSON: [{key, label, model}]
    answers: str = "{}"  # JSON: key -> the model's validated answer
    result: str = "{}"  # JSON: per-portfolio tables and the summary (computed here, never by a model)
    prices: str = "{}"  # JSON: ticker -> closing price on the day of the run, for scoring later
    scored: str = ""  # JSON once the horizon has ended: per model, direction hits and average error
    cost: float = 0.0
    note: str = ""
