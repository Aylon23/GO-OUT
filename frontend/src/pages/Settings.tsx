import clsx from "clsx";
import { X } from "@phosphor-icons/react";
import { useState } from "react";
import { AiSettings } from "../components/AiSettings";
import { Backups } from "../components/Backups";
import { Avatar, refreshLogo } from "../components/Avatar";
import { SymbolSearch } from "../components/SymbolSearch";
import { ErrorLine, Segmented, Skel } from "../components/ui";
import { api, ApiError, type PortfolioInfo, type Settings as S } from "../lib/api";
import { invalidate, useData } from "../lib/hooks";
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
        <Item title="Portfolios" help="Each portfolio has its own holdings and results. Nicknames let you write “קרן קנתה 5 AVGO”. Deleting a portfolio removes its transactions.">
          <PortfolioList />
        </Item>
        <Item title="Telegram" help="Add trades and get reports from your phone. Works while the app is running on this computer.">
          <TelegramSetup />
        </Item>
        <Item title="AI" help="OpenRouter, free models only. Shared by the scenario debate and the stock council. Requests carry tickers, weights and factor exposures, never names or amounts.">
          <AiSettings />
        </Item>
        <Item title="Protection ideas" help="On the What-if page, protection ideas are long-only: trim, rebalance, add bonds or gold, raise cash. Turn this on to also see shorts and inverse funds.">
          <Segmented label="Allow short and inverse hedges" value={settings.allow_short_hedges ? "on" : "off"} onChange={(v) => save({ allow_short_hedges: v === "on" })}
            options={[{ value: "off", label: "Long-only" }, { value: "on", label: "Allow short and inverse hedges" }]} />
        </Item>
        <Item title="Company logos" help="Logos are fetched once from free public sources and kept on this computer. If one is wrong or missing, upload your own (PNG, JPEG, SVG or WebP, up to 300 KB).">
          <LogoOverrides />
        </Item>
        <Item title="Memo language" help="The analyst memo on the What-if page, and its PDF and image card.">
          <Segmented label="Memo language" value={settings.memo_language ?? "he"} onChange={(v) => save({ memo_language: v })} options={[{ value: "he", label: "עברית" }, { value: "en", label: "English" }]} />
        </Item>
        <Item title="Display amounts in" help="Only how amounts are shown, converted at today's rate. Returns and percentages never include currency moves: every stock is measured in its own currency.">
          <Segmented label="Display amounts in" value={settings.base_currency} onChange={(v) => save({ base_currency: v })} options={[{ value: "USD", label: "$ Dollar" }, { value: "ILS", label: "₪ Shekel" }]} />
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
        <Item title="Data" help="Everything lives in data/portfolio.db on this computer, backed up every day.">
          <Backups />
          <div className="mt-8 border-t border-line pt-5">
          {confirmClear ? (
            <div className="flex items-center gap-2">
              <span className="text-sm">Delete every transaction?</span>
              <button type="button" className="btn-ghost" onClick={() => setConfirmClear(false)}>Keep</button>
              <button type="button" className="btn-secondary text-loss" onClick={async () => { await api.clearAll(); setConfirmClear(false); invalidate(); }}>Delete all</button>
            </div>
          ) : <button type="button" className="btn-secondary" onClick={() => setConfirmClear(true)}>Delete all transactions</button>}
          </div>
        </Item>
      </dl>
    </div>
  );
}

function Item({ title, help, children }: { title: string; help: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line py-6 md:grid-cols-[200px_minmax(0,1fr)] md:gap-8">
      <dt><div className="text-sm font-medium">{title}</div><p className="mt-1 text-xs text-muted">{help}</p></dt>
      <dd className="min-w-0">{children}</dd>
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
  const [aliases, setAliases] = useState(p.aliases);
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dirty = name !== p.name || note !== p.note || aliases !== p.aliases;
  const save = async () => { setErr(null); try { await api.updatePortfolio(p.id, name, note, aliases); onChange(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); } };
  return (
    <li className="border-b border-line py-3 first:pt-0">
      <div className="grid gap-2 sm:grid-cols-[1fr_1.3fr_auto] sm:items-start">
        <input aria-label="Portfolio name" className="field h-8 text-sm font-medium" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
        <input aria-label="Note" className="field h-8 text-sm" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <input aria-label="Nicknames" dir="auto" className="field h-8 text-sm sm:col-span-2" placeholder="Nicknames for Ctrl+K and Telegram, e.g. קרן" value={aliases} onChange={(e) => setAliases(e.target.value)} />
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

function TelegramSetup() {
  const q = useData(api.telegram, "telegram");
  const t = q.data;
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // the bot starts in the background; check again shortly after a change
  const refreshSoon = () => setTimeout(() => q.reload(), 2500);
  if (!t) return <Skel className="h-20 w-full" />;
  const saveToken = async (value: string) => {
    setBusy(true); setErr(null);
    try { await api.saveTelegram({ token: value }); setToken(""); q.reload(); refreshSoon(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't save."); } finally { setBusy(false); }
  };
  if (!t.configured) {
    return (
      <div className="max-w-[460px] text-sm">
        <ol className="list-decimal space-y-1 pl-5 text-muted">
          <li>In Telegram, open <span className="font-medium text-text">@BotFather</span> and send <span className="font-mono text-xs text-text">/newbot</span>.</li>
          <li>Pick a name. BotFather replies with a token like <span className="font-mono text-xs text-text">123456789:AAE…</span>.</li>
          <li>Paste it here.</li>
        </ol>
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); saveToken(token); }}>
          <input aria-label="Bot token" className="field h-8 font-mono text-xs" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Bot token" autoComplete="off" />
          <button type="submit" className="btn-secondary h-8 shrink-0" disabled={!token.trim() || busy}>Connect</button>
        </form>
        {err && <p className="error-text">{err}</p>}
      </div>
    );
  }
  return (
    <div className="max-w-[460px] space-y-3 text-sm">
      <p className="flex items-center gap-2">
        <span className={clsx("h-2 w-2 rounded-full", t.running && !t.error ? "bg-gain" : "bg-loss")} aria-hidden />
        {t.running && !t.error ? <>Running{t.username && <> as <span className="font-medium">@{t.username}</span></>}</> : "Not running"}
        <button type="button" className="link ml-auto text-xs" onClick={() => q.reload()}>Check again</button>
      </p>
      {t.error && <p className="text-xs text-loss">{t.error}</p>}
      {t.paired ? (
        <p className="text-muted">Connected to your Telegram. Write to the bot, for example “קרן קנתה 5 AVGO”, or send <span className="font-mono text-xs text-text">/help</span>.</p>
      ) : (
        <div className="rounded-md bg-sunken p-3">
          <p>Send this to {t.username ? <span className="font-medium">@{t.username}</span> : "your bot"} in Telegram:</p>
          <p className="mt-1 select-all font-mono text-base font-semibold">/start {t.pair_code}</p>
          <p className="mt-1 text-xs text-muted">Only the chat that sends this code can use the bot. <button type="button" className="link text-xs" onClick={() => q.reload()}>I sent it</button></p>
        </div>
      )}
      <label className="flex items-center gap-2">
        <input type="checkbox" className="accent-[var(--accent)]" checked={t.weekly_report} onChange={async (e) => { await api.saveTelegram({ weekly_report: e.target.checked }); q.reload(); }} />
        Send me a summary every Sunday morning
      </label>
      <div className="flex flex-wrap gap-3 text-xs">
        {t.paired && <button type="button" className="link text-xs" onClick={async () => { await api.newPairCode(); q.reload(); }}>Connect a different chat</button>}
        <button type="button" className="link text-xs" onClick={() => saveToken("")}>Disconnect the bot</button>
      </div>
    </div>
  );
}

function LogoOverrides() {
  const q = useData(api.logoOverrides, "logo-overrides");
  const [sym, setSym] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [inputKey, setInputKey] = useState(0);
  const upload = async () => {
    const s = sym.trim().toUpperCase();
    if (!s || !file) return;
    setBusy(true); setErr(null);
    try { await api.uploadLogo(s, file); refreshLogo(s); setSym(""); setFile(null); setInputKey((k) => k + 1); q.reload(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't upload it."); } finally { setBusy(false); }
  };
  return (
    <div>
      {(q.data ?? []).length > 0 && (
        <ul className="mb-3 flex flex-wrap gap-2">
          {q.data!.map((s) => (
            <li key={s} className="flex h-8 items-center gap-2 rounded-sm border border-line bg-surface pl-1.5 pr-1 text-sm">
              <Avatar ticker={s} size={20} /><span className="font-mono font-semibold">{s}</span>
              <button type="button" aria-label={`Remove the logo for ${s}`} className="btn-ghost h-6 w-6 px-0" onClick={async () => { await api.clearLogo(s); refreshLogo(s); q.reload(); }}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); upload(); }}>
        <input aria-label="Symbol" className="field h-9 w-28 font-mono uppercase" placeholder="SEDG" value={sym} onChange={(e) => setSym(e.target.value)} />
        <input key={inputKey} aria-label="Logo image" type="file" accept="image/png,image/jpeg,image/svg+xml,image/webp,image/gif" className="text-sm text-muted file:mr-3 file:h-9 file:rounded-sm file:border file:border-line-strong file:bg-surface file:px-3 file:text-sm file:text-text"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button type="submit" className="btn-secondary" disabled={!sym.trim() || !file || busy}>{busy ? "Uploading" : "Use this logo"}</button>
      </form>
      {err && <p className="error-text">{err}</p>}
    </div>
  );
}
