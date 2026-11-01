import { X } from "@phosphor-icons/react";
import { useState } from "react";
import { Avatar } from "../components/Avatar";
import { SymbolSearch } from "../components/SymbolSearch";
import { ErrorLine, Segmented } from "../components/ui";
import { api, ApiError, type Settings as S } from "../lib/api";
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
