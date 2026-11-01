import { ArrowsDownUp } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Ccy, type SymbolItem, type Tx, type TxType } from "../lib/api";
import { ccySymbol, dateLong, money, num, today } from "../lib/format";
import { Avatar } from "./Avatar";
import { SymbolSearch } from "./SymbolSearch";
import { Segmented } from "./ui";

const TYPES: { value: TxType; label: string }[] = [
  { value: "BUY", label: "Buy" }, { value: "SELL", label: "Sell" }, { value: "DIVIDEND", label: "Dividend" },
  { value: "DEPOSIT", label: "Deposit" }, { value: "WITHDRAWAL", label: "Withdrawal" },
];

interface Props {
  initial?: Tx;
  symbol?: SymbolItem | null;
  baseCurrency: Ccy;
  onDone: (t: Tx) => void;
  onCancel: () => void;
}

const toNum = (s: string) => { const n = Number(s.replace(/,/g, "")); return s.trim() === "" || !isFinite(n) ? NaN : n; };
const fmtInput = (n: number, d = 4) => (isFinite(n) ? String(Number(n.toFixed(d))) : "");

export function TxForm({ initial, symbol, baseCurrency, onDone, onCancel }: Props) {
  const [type, setType] = useState<TxType>(initial?.type ?? "BUY");
  const [sym, setSym] = useState<SymbolItem | null>(
    initial?.ticker ? { symbol: initial.ticker, name: initial.ticker, exchange: "", type: "", currency: initial.currency } : symbol ?? null,
  );
  const [currency, setCurrency] = useState<Ccy>(initial?.currency ?? ((symbol?.currency as Ccy) || baseCurrency));
  const [date, setDate] = useState(initial?.date ?? today());
  const [price, setPrice] = useState(initial && (initial.type === "BUY" || initial.type === "SELL") ? String(initial.price) : "");
  const [qty, setQty] = useState(initial?.quantity ? String(initial.quantity) : "");
  const [amount, setAmount] = useState(initial && !(initial.type === "BUY" || initial.type === "SELL") ? String(initial.price) : "");
  const [mode, setMode] = useState<"quantity" | "amount">("quantity");
  const [fees, setFees] = useState(initial?.fees ? String(initial.fees) : "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [priceInfo, setPriceInfo] = useState<{ text: string; error?: boolean } | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const priceTouched = useRef(!!initial);
  const first = useRef(true);

  const trade = type === "BUY" || type === "SELL";
  const needsSymbol = type !== "DEPOSIT" && type !== "WITHDRAWAL";

  // price auto-fill with that day's close
  useEffect(() => {
    if (first.current && initial) { first.current = false; return; }
    first.current = false;
    if (!trade || !sym || !date) { setPriceInfo(null); return; }
    priceTouched.current = false;
    let live = true;
    setPriceInfo({ text: "Looking up that day's close" });
    api.price(sym.symbol, date)
      .then((p) => {
        if (!live) return;
        if (!priceTouched.current) setPrice(fmtInput(p.close, 2));
        setCurrency(p.currency);
        setPriceInfo({ text: p.date === date ? `Close on ${dateLong(p.date)}. Edit for your exact fill price.` : `No trading on that day. Close on ${dateLong(p.date)}, the session before.` });
      })
      .catch((e) => { if (live) setPriceInfo({ text: e instanceof ApiError ? e.message : "Couldn't load the price.", error: true }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sym?.symbol, date, trade]);

  const p = toNum(price);
  const q = mode === "quantity" ? toNum(qty) : toNum(amount) / p;
  const a = mode === "amount" ? toNum(amount) : toNum(qty) * p;
  const cashAmount = toNum(amount);

  const preview = (() => {
    const c = currency;
    if (trade) {
      if (!sym || !(q > 0) || !(p > 0)) return null;
      return `${type === "BUY" ? "Buy" : "Sell"} ${num(q, 4)} ${sym.symbol} at ${money(p, c, { decimals: 2 })} = ${money(q * p, c)} on ${dateLong(date)}`;
    }
    if (!(cashAmount > 0)) return null;
    if (type === "DIVIDEND") return sym ? `Dividend of ${money(cashAmount, c)} from ${sym.symbol} on ${dateLong(date)}` : null;
    return `${type === "DEPOSIT" ? "Deposit" : "Withdraw"} ${money(cashAmount, c)} on ${dateLong(date)}`;
  })();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (needsSymbol && !sym) errs.ticker = "Pick a symbol.";
    if (!date) errs.date = "Pick a date.";
    if (trade) {
      if (!(p > 0)) errs.price = "Enter a price above 0.";
      if (!(q > 0)) errs[mode] = mode === "quantity" ? "Enter a quantity above 0." : "Enter an amount above 0.";
    } else if (!(cashAmount > 0)) errs.amount = "Enter an amount above 0.";
    setErrors(errs);
    setFormError(null);
    if (Object.keys(errs).length) return;
    const body: Tx = {
      ticker: needsSymbol ? sym!.symbol : "",
      type, date, currency,
      quantity: trade ? Number(q.toFixed(6)) : 0,
      price: trade ? p : cashAmount,
      fees: toNum(fees) > 0 ? toNum(fees) : 0,
      note: note.trim(),
    };
    setSaving(true);
    try {
      const saved = initial?.id ? await api.updateTx(initial.id, body) : await api.createTx(body);
      onDone(saved);
    } catch (err) {
      if (err instanceof ApiError) {
        const f = { ...err.fields };
        if (f.price && !trade) { f.amount = f.price; delete f.price; }
        if (f.quantity && mode === "amount") { f.amount = f.quantity; delete f.quantity; }
        setErrors(f);
        setFormError(Object.keys(f).length ? null : err.message);
      } else setFormError("Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  const Err = ({ k }: { k: string }) => (errors[k] ? <p className="error-text" role="alert">{errors[k]}</p> : null);
  const sign = ccySymbol(currency);

  return (
    <form onSubmit={submit} noValidate className="fade-in max-w-[760px]" onKeyDown={(e) => { if (e.key === "Escape" && (e.target as HTMLElement).tagName !== "INPUT") onCancel(); }}>
      <Segmented label="Transaction type" options={TYPES} value={type} onChange={(t) => { setType(t); setErrors({}); }} />

      <div className="mt-6 grid gap-x-6 gap-y-5 md:grid-cols-2">
        {needsSymbol && (
          <div className="md:col-span-2">
            {sym ? (
              <div>
                <span className="label">Symbol</span>
                <div className="flex h-9 items-center gap-3">
                  <Avatar ticker={sym.symbol} size={24} />
                  <span className="font-mono text-sm font-semibold">{sym.symbol}</span>
                  {sym.name !== sym.symbol && <span className="truncate text-sm text-muted">{sym.name}</span>}
                  <button type="button" className="link ml-auto shrink-0 text-sm" onClick={() => { setSym(null); setPrice(""); setPriceInfo(null); }}>Change</button>
                </div>
              </div>
            ) : (
              <SymbolSearch label="Symbol" autoFocus={!initial} invalid={!!errors.ticker}
                onSelect={(s) => { setSym(s); if (s.currency === "USD" || s.currency === "ILS") setCurrency(s.currency); setErrors((e) => ({ ...e, ticker: "" })); }} />
            )}
            <Err k="ticker" />
          </div>
        )}

        <div>
          <label className="label" htmlFor="tx-date">Date</label>
          <input id="tx-date" type="date" className={clsx("field", errors.date && "border-loss")} value={date} max={today()} onChange={(e) => setDate(e.target.value)} />
          <Err k="date" />
        </div>

        <div>
          <label className="label" htmlFor="tx-ccy">Currency</label>
          <select id="tx-ccy" className="field" value={currency} onChange={(e) => setCurrency(e.target.value as Ccy)}>
            <option value="USD">USD ($)</option>
            <option value="ILS">ILS (₪)</option>
          </select>
          {needsSymbol && sym && <p className="helper">Set from {sym.symbol}'s listing.</p>}
          <Err k="currency" />
        </div>

        {trade ? (
          <>
            <div>
              <label className="label" htmlFor="tx-price">Price per share</label>
              <div className="relative">
                <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-faint">{sign}</span>
                <input id="tx-price" inputMode="decimal" className={clsx("field num pl-7", errors.price && "border-loss")} value={price}
                  onChange={(e) => { priceTouched.current = true; setPrice(e.target.value); }} placeholder="0.00" />
              </div>
              {priceInfo && !errors.price && <p className={priceInfo.error ? "error-text" : "helper"}>{priceInfo.text}</p>}
              <Err k="price" />
            </div>
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <label className="text-sm font-medium" htmlFor="tx-qa">{mode === "quantity" ? "Quantity" : "Amount invested"}</label>
                <button type="button" className="press flex items-center gap-1 rounded-sm text-xs text-muted hover:text-text"
                  onClick={() => {
                    if (mode === "quantity") { setAmount(isFinite(a) ? fmtInput(a, 2) : ""); setMode("amount"); }
                    else { setQty(isFinite(q) ? fmtInput(q, 6) : ""); setMode("quantity"); }
                  }}>
                  <ArrowsDownUp size={12} /> Enter {mode === "quantity" ? "amount" : "quantity"} instead
                </button>
              </div>
              <div className="relative">
                {mode === "amount" && <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-faint">{sign}</span>}
                <input id="tx-qa" inputMode="decimal" className={clsx("field num", mode === "amount" && "pl-7", (errors.quantity || errors.amount) && "border-loss")}
                  value={mode === "quantity" ? qty : amount} placeholder="0"
                  onChange={(e) => (mode === "quantity" ? setQty(e.target.value) : setAmount(e.target.value))} />
              </div>
              <p className="helper num">
                {mode === "quantity" ? (isFinite(a) && a > 0 ? `= ${money(a, currency)}` : "Fractional shares are fine.") : isFinite(q) && q > 0 ? `= ${num(q, 4)} shares` : "We work out the shares from the price."}
              </p>
              <Err k="quantity" /><Err k="amount" />
            </div>
          </>
        ) : (
          <div>
            <label className="label" htmlFor="tx-amount">{type === "DIVIDEND" ? "Amount received" : "Amount"}</label>
            <div className="relative">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-faint">{sign}</span>
              <input id="tx-amount" inputMode="decimal" className={clsx("field num pl-7", errors.amount && "border-loss")} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" />
            </div>
            {type === "DIVIDEND" && <p className="helper">After tax. Once you log one, automatic dividends stop for this symbol.</p>}
            <Err k="amount" />
          </div>
        )}

        {type !== "DEPOSIT" && type !== "WITHDRAWAL" && (
          <div>
            <label className="label" htmlFor="tx-fees">Fees <span className="font-normal text-faint">(optional)</span></label>
            <input id="tx-fees" inputMode="decimal" className={clsx("field num", errors.fees && "border-loss")} value={fees} onChange={(e) => setFees(e.target.value)} placeholder="0" />
            <Err k="fees" />
          </div>
        )}
        <div className={clsx(type === "DEPOSIT" || type === "WITHDRAWAL" ? "" : "md:col-span-2")}>
          <label className="label" htmlFor="tx-note">Note <span className="font-normal text-faint">(optional)</span></label>
          <input id="tx-note" className="field" value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} />
        </div>
      </div>

      <div className="mt-6 flex flex-col gap-4 border-t border-line pt-4 md:flex-row md:items-center md:justify-between">
        <p className="num min-h-[24px] text-sm text-muted" aria-live="polite">{preview ?? "Fill in the fields to see a preview."}</p>
        <div className="flex shrink-0 gap-2">
          <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={saving}>{saving ? "Saving" : initial?.id ? "Save changes" : "Add transaction"}</button>
        </div>
      </div>
      {formError && <p className="error-text" role="alert">{formError}</p>}
    </form>
  );
}
