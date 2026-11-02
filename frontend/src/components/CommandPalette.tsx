import { Check, Lightning } from "@phosphor-icons/react";
import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type OmniDraft } from "../lib/api";
import { dateLong, money, num } from "../lib/format";
import { invalidate, useDebounced } from "../lib/hooks";
import { useApp } from "../state";
import { SymbolSearch } from "./SymbolSearch";

const VERB = /(קני|קנה|קנת|קנו|מכר|הפקד|משכ|משך|דיבידנד|\bbuy\b|\bbought\b|\bsell\b|\bsold\b|\bdeposit|\bwithdr|\bdividend)/i;
/** A sentence ("bought 5 QQQ"), not a symbol search. */
export const looksLikeCommand = (q: string) => VERB.test(q) || (/\d/.test(q) && q.trim().split(/\s+/).length >= 2);

const TYPE_WORD: Record<string, string> = { BUY: "Buy", SELL: "Sell", DIVIDEND: "Dividend", DEPOSIT: "Deposit", WITHDRAWAL: "Withdraw" };

export function CommandPalette() {
  const { paletteOpen, setPaletteOpen, openQuickView, reloadPortfolios } = useApp();
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<OmniDraft | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const command = looksLikeCommand(query);
  const debounced = useDebounced(command ? query : "", 350);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaletteOpen(!paletteOpen); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [paletteOpen, setPaletteOpen]);

  useEffect(() => { if (!paletteOpen) { setQuery(""); setDraft(null); setErr(null); setDone(null); } }, [paletteOpen]);

  useEffect(() => {
    if (!debounced.trim()) { setDraft(null); return; }
    const ctl = new AbortController();
    setErr(null);
    api.omnibox(debounced, ctl.signal).then(setDraft).catch((e) => { if (e.name !== "AbortError") setErr(e instanceof ApiError ? e.message : "Couldn't read that."); });
    return () => ctl.abort();
  }, [debounced]);

  const ready = !!draft && draft.missing.length === 0 && debounced === query;
  const save = useCallback(async () => {
    if (!draft || !ready || busy) return;
    setBusy(true); setErr(null);
    try {
      await api.createTxIn(draft.portfolio_id, {
        ticker: draft.ticker, type: draft.type, date: draft.date, quantity: draft.quantity ?? 0, price: draft.price ?? 0,
        currency: draft.currency, fees: 0, note: draft.text.slice(0, 200),
      });
      setDone(`Added to ${draft.portfolio_name}: ${describe(draft)}`);
      setDraft(null); setQuery(""); setInputKey((k) => k + 1);
      invalidate(); reloadPortfolios().catch(() => {});
    } catch (e) {
      setErr(e instanceof ApiError ? Object.values(e.fields).join(" ") || e.message : "Couldn't save.");
    } finally { setBusy(false); }
  }, [draft, ready, busy, reloadPortfolios]);

  const onQuery = useCallback((q: string) => { setQuery(q); if (q) setDone(null); }, []);
  if (!paletteOpen) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[12dvh]">
      <div className="fade-in absolute inset-0 bg-scrim" onClick={() => setPaletteOpen(false)} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label="Search or add a transaction" className="pop-in relative w-full max-w-[640px] overflow-hidden rounded-md bg-surface shadow-panel">
        <SymbolSearch key={inputKey} variant="list" size="lg" autoFocus placeholder="Search a stock, or type: bought 5 QQQ"
          onEscape={() => setPaletteOpen(false)} onQueryChange={onQuery} suppressResults={command} onSubmitQuery={save}
          onSelect={(s) => { setPaletteOpen(false); openQuickView(s); }} />
        {command && <DraftCard draft={draft} pending={debounced !== query} err={err} busy={busy} ready={ready} onSave={save} />}
        {done && !command && (
          <p className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm" role="status"><Check size={16} className="text-gain" />{done}</p>
        )}
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-xs text-faint">
          {command ? <><span><kbd className="font-sans">Enter</kbd> add</span><span><kbd className="font-sans">Esc</kbd> close</span></>
            : <><span><kbd className="font-sans">↑↓</kbd> move</span><span><kbd className="font-sans">Enter</kbd> open</span><span><kbd className="font-sans">Esc</kbd> close</span>
              <span className="ml-auto">Or type a trade: “קניתי 5 מניות של QQQ”, “Dad bought SPY for $1000”</span></>}
        </div>
      </div>
    </div>
  );
}

function describe(d: OmniDraft) {
  if (d.type === "BUY" || d.type === "SELL") {
    const total = (d.quantity ?? 0) * (d.price ?? 0);
    return `${TYPE_WORD[d.type]} ${num(d.quantity, 4)} ${d.ticker} at ${money(d.price, d.currency, { decimals: 2 })} = ${money(total, d.currency)} on ${dateLong(d.date)}`;
  }
  if (d.type === "DIVIDEND") return `Dividend of ${money(d.price, d.currency)} from ${d.ticker || "?"} on ${dateLong(d.date)}`;
  return `${TYPE_WORD[d.type]} ${money(d.price, d.currency)} on ${dateLong(d.date)}`;
}

const MISSING: Record<string, string> = {
  ticker: "Which stock? Add a ticker like QQQ or a name like אפל.",
  quantity: "How many? Add shares (“5 מניות”) or an amount (“ב-1000 דולר”).",
  amount: "How much? Add an amount like “5000 שקל”.",
};

function DraftCard({ draft, pending, err, busy, ready, onSave }: {
  draft: OmniDraft | null; pending: boolean; err: string | null; busy: boolean; ready: boolean; onSave: () => void;
}) {
  return (
    <div className="border-b border-line px-4 py-4" aria-live="polite">
      {!draft ? (
        <div className="space-y-2" aria-hidden><div className="skeleton h-4 w-3/4" /><div className="skeleton h-3 w-1/3" /></div>
      ) : (
        <div className={clsx("transition-opacity duration-fast", pending && "opacity-50")}>
          <div className="flex items-start gap-3">
            <Lightning size={18} className="mt-0.5 shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <p className="num text-base">{draft.missing.length ? <span className="text-muted">{TYPE_WORD[draft.type]} {draft.ticker || "…"}</span> : describe(draft)}</p>
              <p className="mt-1 text-sm text-muted">
                Into <span className="font-medium text-text">{draft.portfolio_name}</span>
                {draft.price_source === "close" && " · price is that day's close"}
              </p>
              {draft.missing.map((m) => <p key={m} className="mt-2 text-sm text-loss">{MISSING[m]}</p>)}
              {draft.warnings.map((w) => <p key={w} className="mt-1 text-xs text-faint">{w}</p>)}
            </div>
            <button type="button" className="btn-primary shrink-0" disabled={!ready || busy} onClick={onSave}>{busy ? "Adding" : "Add"}</button>
          </div>
        </div>
      )}
      {err && <p className="error-text" role="alert">{err}</p>}
    </div>
  );
}
