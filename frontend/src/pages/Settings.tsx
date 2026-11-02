import { X } from "@phosphor-icons/react";
import { useState } from "react";
import { Avatar } from "../components/Avatar";
import { SymbolSearch } from "../components/SymbolSearch";
import { ErrorLine, Segmented } from "../components/ui";
import { api, ApiError, type PortfolioInfo, type Settings as S } from "../lib/api";
import { invalidate } from "../lib/hooks";
import { useApp } from "../state";

export default function Settings() {
  const { settings, saveSettings } = useApp();
  const [err, setErr] = useState<string | null>(null);
  const [rf, setRf] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  if (!settings) return null;
  const save = async (p: Partial<S>) => { setErr(null); try { await saveSettings(p); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); } };
  const rfVal = rf ?? (settings.risk_free * 100).toFixed(1);
  return (
    <div className="mx-auto max-w-[720px] px-4 pb-24 pt-8 md:px-8 md:pt-12">
      <h1 className="text-xl font-semibold">Settings</h1>
      {err && <ErrorLine message={err} />}
      <dl className="mt-8">
        <Item title="Portfolios" help="Each portfolio has its own holdings and results. Deleting one removes its transactions.">
          <PortfolioList />
        </Item>
        <Item title="Base currency" help="Everything is converted into this currency using each day's exchange rate.">
          <Segmented label="Base currency" value={settings.base_currency} onChange={(v) => save({ base_currency: v })} options={[{ value: "ILS", label: "₪ Shekel" }, { value: "USD", label: "$ Dollar" }]} />
        </Item>
        <Item title="Benchmarks" help="Compared everywhere using total return (dividends reinvested). SPY and QQQ by default; add any ticker, like ^TA125.TA.">
          <ul className="mb-3 flex flex-wrap gap-2">
            {settings.benchmarks.map((b) => (
              <li key={b} className="flex h-8 items-center gap-2 rounded-sm border border-line bg-surface pl-2 pr-1 text-sm">
                <Avatar ticker={b} size={20} /><span className="font-mono font-semibold">{b}</span>
                <button type="button" aria-label={`Remove ${b}`} className="btn-ghost h-6 w-6 px-0" onClick={() => save({ benchmarks: settings.benchmarks.filter((x) => x !== b) })}><X size={12} /></button>
              </li>
            ))}
          </ul>
          <div className="max-w-[360px]"><SymbolSearch placeholder="Add a benchmark" exclude={settings.benchmarks} onSelect={(s) => save({ benchmarks: [...settings.benchmarks, s.symbol] })} /></div>
        </Item>
        <Item title="Risk-free rate" help="Used for the Sharpe ratio. Roughly what cash or short bonds pay.">
          <div className="relative w-28">
            <input aria-label="Risk-free rate" inputMode="decimal" className="field num pr-7 text-right" value={rfVal} onChange={(e) => setRf(e.target.value)}
              onBlur={() => { const v = Number(rfVal); if (isFinite(v)) save({ risk_free: v / 100 }); setRf(null); }} />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-faint">%</span>
          </div>
        </Item>
        <Item title="Dividends" help="When on, cash dividends from the price feed are added for holdings where you haven't logged dividends yourself.">
          <Segmented label="Automatic dividends" value={settings.auto_dividends ? "on" : "off"} onChange={(v) => save({ auto_dividends: v === "on" })} options={[{ value: "on", label: "Automatic" }, { value: "off", label: "Only what I log" }]} />
        </Item>
        <Item title="Theme" help="System follows your device.">
          <Segmented label="Theme" value={settings.theme} onChange={(v) => save({ theme: v })} options={[{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" }]} />
        </Item>
        <Item title="Data" help="Everything lives in data/portfolio.db on this computer. Export a CSV first if you want a copy.">
          {confirmClear ? (
            <div className="flex items-center gap-2">
              <span className="text-sm">Delete every transaction?</span>
              <button type="button" className="btn-ghost" onClick={() => setConfirmClear(false)}>Keep</button>
              <button type="button" className="btn-secondary text-loss" onClick={async () => { await api.clearAll(); setConfirmClear(false); invalidate(); }}>Delete all</button>
            </div>
          ) : <button type="button" className="btn-secondary" onClick={() => setConfirmClear(true)}>Delete all transactions</button>}
        </Item>
      </dl>
    </div>
  );
}

function Item({ title, help, children }: { title: string; help: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-3 border-t border-line py-6 md:grid-cols-[200px_1fr] md:gap-8">
      <dt><div className="text-sm font-medium">{title}</div><p className="mt-1 text-xs text-muted">{help}</p></dt>
      <dd>{children}</dd>
    </div>
  );
}

function PortfolioList() {
  const { portfolios, reloadPortfolios } = useApp();
  return (
    <div>
      <ul>{portfolios.map((p) => <PortfolioRow key={p.id} p={p} canDelete={portfolios.length > 1} onChange={() => { reloadPortfolios(); invalidate(); }} />)}</ul>
      <NewPortfolioRow onCreated={() => reloadPortfolios()} />
    </div>
  );
}

function PortfolioRow({ p, canDelete, onChange }: { p: PortfolioInfo; canDelete: boolean; onChange: () => void }) {
  const [name, setName] = useState(p.name);
  const [note, setNote] = useState(p.note);
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dirty = name !== p.name || note !== p.note;
  const save = async () => { setErr(null); try { await api.updatePortfolio(p.id, name, note); onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); } };
  return (
    <li className="border-b border-line py-3 first:pt-0">
      <div className="grid gap-2 sm:grid-cols-[1fr_1.3fr_auto] sm:items-center">
        <input aria-label="Portfolio name" className="field h-8 text-sm font-medium" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
        <input aria-label="Note" className="field h-8 text-sm" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="flex justify-end gap-1">
          {dirty && <button type="button" className="btn-secondary h-8" onClick={save}>Save</button>}
          {canDelete && (confirm ? (
            <>
              <button type="button" className="btn-ghost h-8" onClick={() => setConfirm(false)}>Keep</button>
              <button type="button" className="btn-secondary h-8 text-loss" onClick={async () => { await api.deletePortfolio(p.id); onChange(); }}>Delete</button>
            </>
          ) : <button type="button" aria-label={`Delete ${p.name}`} className="btn-ghost h-8 w-8 px-0" onClick={() => setConfirm(true)}><X size={14} /></button>)}
        </div>
      </div>
      <p className="mt-1 text-xs text-faint">{p.transactions} transaction{p.transactions === 1 ? "" : "s"}{confirm && ". Deleting removes them all."}</p>
      {err && <p className="error-text">{err}</p>}
    </li>
  );
}

function NewPortfolioRow({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <form className="mt-3 flex gap-2" onSubmit={async (e) => {
      e.preventDefault(); setErr(null);
      try { await api.createPortfolio(name); setName(""); onCreated(); } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't create it."); }
    }}>
      <input aria-label="New portfolio name" className="field h-8 text-sm" placeholder="New portfolio name" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
      <button type="submit" className="btn-secondary h-8 shrink-0" disabled={!name.trim()}>Add</button>
      {err && <p className="error-text">{err}</p>}
    </form>
  );
}
