import { Check, Lightning } from "@phosphor-icons/react";
import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type OmniDraft } from "../lib/api";
import { dateLong, money, num, today } from "../lib/format";
import { invalidate, useDebounced } from "../lib/hooks";
import { useApp } from "../state";
import { Avatar } from "./Avatar";
import { SymbolSearch } from "./SymbolSearch";

const VERB = /(קני|קנה|קנת|קנו|מכר|הפקד|משכ|משך|דיבידנד|\bbuy\b|\bbought\b|\bsell\b|\bsold\b|\bdeposit|\bwithdr|\bdividend)/i;
/** A sentence ("bought 5 QQQ"), not a symbol search. */
export const looksLikeCommand = (q: string) => VERB.test(q) || (/\d/.test(q) && q.trim().split(/\s+/).length >= 2);

const TYPE_WORD: Record<string, string> = { BUY: "Buy", SELL: "Sell", DIVIDEND: "Dividend", DEPOSIT: "Deposit", WITHDRAWAL: "Withdraw" };

export function CommandPalette() {
  const { paletteOpen, setPaletteOpen, openQuickView, reloadPortfolios, portfolios } = useApp();
  const [picked, setPicked] = useState<number[] | null>(null); // portfolio picker answer
  const [choose, setChoose] = useState<Record<string, string | number>>({}); // "Did you mean…" answers
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<OmniDraft | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [logCall, setLogCall] = useState(true);
  const command = looksLikeCommand(query);
  const debounced = useDebounced(command ? query : "", 350);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaletteOpen(!paletteOpen); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [paletteOpen, setPaletteOpen]);

  useEffect(() => { if (!paletteOpen) { setQuery(""); setDraft(null); setErr(null); setDone(null); setPicked(null); setChoose({}); } }, [paletteOpen]);
  // a new sentence starts fresh: no carried-over picks
  useEffect(() => { setPicked(null); setChoose({}); }, [debounced]);

  useEffect(() => {
    if (!debounced.trim()) { setDraft(null); return; }
    const ctl = new AbortController();
    setErr(null);
    api.omnibox(debounced, { today: today(), portfolio_ids: picked ?? undefined, choose }, ctl.signal)
      .then(setDraft).catch((e) => { if (e.name !== "AbortError") setErr(e instanceof ApiError ? e.message : "Couldn't read that."); });
    return () => ctl.abort();
  }, [debounced, picked, choose]);

  const ready = !!draft && draft.missing.length === 0 && debounced === query;
  const save = useCallback(async () => {
    if (!draft || !ready || busy) return;
    setBusy(true); setErr(null);
    try {
      const out = await api.omniCommit(draft, logCall);
      const parts = [];
      if (out.saved.length) parts.push(`Added to ${out.saved.map((x) => x.portfolio).join(", ")}: ${describe(draft)}`);
      if (out.failed.length) parts.push(`Not added: ${out.failed.map((f) => `${f.portfolio} (${f.error})`).join("; ")}`);
      if (out.rec_id) parts.push("Saved as a recommendation.");
      setDone(parts.join(" "));
      setDraft(null); setQuery(""); setInputKey((k) => k + 1);
      invalidate(); reloadPortfolios().catch(() => {});
    } catch (e) {
      setErr(e instanceof ApiError ? Object.values(e.fields).join(" ") || e.message : "Couldn't save.");
    } finally { setBusy(false); }
  }, [draft, ready, busy, reloadPortfolios, logCall]);

  const onQuery = useCallback((q: string) => { setQuery(q); if (q) setDone(null); }, []);
  if (!paletteOpen) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[12dvh]">
      <div className="fade-in absolute inset-0 bg-scrim" onClick={() => setPaletteOpen(false)} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label="Search or add a transaction" className="pop-in relative w-full max-w-[640px] overflow-hidden rounded-md bg-surface shadow-panel">
        <SymbolSearch key={inputKey} variant="list" size="lg" autoFocus placeholder="Search a stock, or type: bought 5 QQQ"
          onEscape={() => setPaletteOpen(false)} onQueryChange={onQuery} suppressResults={command} onSubmitQuery={save}
          onSelect={(s) => { setPaletteOpen(false); openQuickView(s); }} />
        {command && <DraftCard draft={draft} pending={debounced !== query} err={err} busy={busy} ready={ready} onSave={save} logCall={logCall} setLogCall={setLogCall}
          portfolios={portfolios} onPick={setPicked} onChoose={(k, v) => setChoose((c) => ({ ...c, [k]: v }))} />}
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
    const live = d.targets.filter((t) => !t.skip);
    const same = live.every((t) => t.quantity === live[0]?.quantity);
    const q = live.length && same ? live[0].quantity : d.quantity;
    if (live.length > 1 && !same) return `${TYPE_WORD[d.type]} ${d.ticker} at ${money(d.price, d.currency, { decimals: 2 })} on ${dateLong(d.date)}`;
    const total = (q ?? 0) * (d.price ?? 0);
    return `${TYPE_WORD[d.type]} ${num(q, 4)} ${d.ticker} at ${money(d.price, d.currency, { decimals: 2 })} = ${money(total, d.currency)} on ${dateLong(d.date)}${live.length > 1 ? " each" : ""}`;
  }
  if (d.type === "DIVIDEND") return `Dividend of ${money(d.price, d.currency)} from ${d.ticker || "?"} on ${dateLong(d.date)}`;
  return `${TYPE_WORD[d.type]} ${money(d.price, d.currency)} on ${dateLong(d.date)}`;
}

const MISSING: Record<string, string> = {
  ticker: "Which stock? Add a ticker like QQQ or a name like אפל.",
  quantity: "How many? Add shares (“5 מניות”) or an amount (“ב-1000 דולר”).",
  amount: "How much? Add an amount like “5000 שקל”.",
  position: "None of these portfolios holds it.",
};

function DraftCard({ draft, pending, err, busy, ready, onSave, logCall, setLogCall, portfolios, onPick, onChoose }: {
  draft: OmniDraft | null; pending: boolean; err: string | null; busy: boolean; ready: boolean; onSave: () => void;
  logCall: boolean; setLogCall: (v: boolean) => void;
  portfolios: { id: number; name: string }[]; onPick: (ids: number[]) => void; onChoose: (k: string, v: string | number) => void;
}) {
  return (
    <div className="border-b border-line px-4 py-4" aria-live="polite">
      {!draft ? (
        <div className="space-y-2" aria-hidden><div className="skeleton h-4 w-3/4" /><div className="skeleton h-3 w-1/3" /></div>
      ) : (
        <div className={clsx("transition-opacity duration-fast", pending && "opacity-50")}>
          <div className="flex items-start gap-3">
            {draft.ticker ? <span className="mt-0.5"><Avatar ticker={draft.ticker} size={20} /></span> : <Lightning size={18} className="mt-0.5 shrink-0 text-accent" />}
            <div className="min-w-0 flex-1">
              <p className="num text-base">{draft.missing.length ? <span className="text-muted">{TYPE_WORD[draft.type]} {draft.ticker || "…"}</span> : describe(draft)}</p>
              <p className="mt-1 text-sm text-muted">
                {draft.targets.length > 0 && "Into "}{draft.targets.filter((t) => !t.skip).map((t, i) => (
                  <span key={t.portfolio_id}>{i > 0 && ", "}<span className="font-medium text-text">{t.portfolio_name}</span>
                    {draft.type === "SELL" && t.quantity != null && <span className="num"> ({num(t.quantity, 4)})</span>}</span>))}
                {draft.price_source === "close" && draft.price_date && `${draft.targets.length ? " · " : ""}Price as of ${dateLong(draft.price_date)}`}
              </p>
              {draft.targets.filter((t) => t.skip).map((t) => (
                <p key={t.portfolio_id} className="mt-1 text-xs text-faint">Skips {t.portfolio_name}: {t.reason}.</p>
              ))}

              {(draft.type === "BUY" || draft.type === "SELL") && (
                <label className="mt-2 flex items-center gap-2 text-sm text-muted">
                  <input type="checkbox" className="accent-[var(--accent)]" checked={logCall} onChange={(e) => setLogCall(e.target.checked)} />
                  Save as my recommendation
                </label>
              )}
              {draft.choices.ticker && (
                <div className="mt-3"><p className="text-sm">Did you mean…</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">{draft.choices.ticker.map((t) => (
                    <button key={t} type="button" className="btn-secondary h-8 font-mono" onClick={() => onChoose("ticker", t)}>{t}</button>))}</div></div>
              )}
              {draft.choices.quantity && (
                <div className="mt-3"><p className="text-sm">How many shares did you mean?</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">{draft.choices.quantity.map((q) => (
                    <button key={q} type="button" className="btn-secondary h-8 num" onClick={() => onChoose("quantity", q)}>{num(q, 4)}</button>))}</div></div>
              )}
              {draft.missing.includes("portfolio") && (
                <div className="mt-3"><p className="text-sm">Whose portfolio? <span className="text-xs text-faint">No name in the text matched a portfolio.</span></p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {portfolios.map((p) => <button key={p.id} type="button" className="btn-secondary h-8" onClick={() => onPick([p.id])}>{p.name}</button>)}
                    <button type="button" className="btn-ghost h-8" onClick={() => onPick(portfolios.map((p) => p.id))}>Everyone</button>
                  </div></div>
              )}
              {draft.missing.filter((m) => m !== "portfolio" && m !== "choice").map((m) => <p key={m} className="mt-2 text-sm text-loss">{MISSING[m]}</p>)}
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
