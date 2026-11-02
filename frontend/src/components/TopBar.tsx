import { Gear, MagnifyingGlass, Monitor, Moon, Sun } from "@phosphor-icons/react";
import clsx from "clsx";
import { type Route, useApp } from "../state";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

const NAV: { route: Route; label: string }[] = [
  { route: "dashboard", label: "Overview" },
  { route: "holdings", label: "Holdings" },
  { route: "risk", label: "Risk" },
  { route: "compare", label: "Compare" },
  { route: "transactions", label: "Transactions" },
  { route: "whatif", label: "What-if" },
];

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

export function TopBar() {
  const { route, go, setPaletteOpen, settings, saveSettings } = useApp();
  const theme = settings?.theme ?? "system";
  const nextTheme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const ThemeIcon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg">
      <div className="mx-auto flex max-w-content flex-wrap items-center gap-x-2 px-4 md:h-14 md:flex-nowrap md:gap-6 md:px-8">
        <a href="#/dashboard" onClick={(e) => { e.preventDefault(); go("dashboard"); }} className="flex h-14 shrink-0 items-center gap-2 rounded-sm font-semibold md:h-auto">
          <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="8" fill="var(--accent)" /><path d="M7 21l6-6 4 4 8-9" stroke="var(--accent-text)" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span className="hidden sm:inline">Benchmark</span>
        </a>
        <span aria-hidden className="hidden h-5 border-l border-line sm:block" />
        <PortfolioSwitcher />
        <nav aria-label="Pages" className="order-last -mx-2.5 flex w-[calc(100%+20px)] items-center overflow-x-auto pb-2 md:order-none md:mx-0 md:w-auto md:min-w-0 md:flex-1 md:pb-0">
          {NAV.map((n) => (
            <a key={n.route} href={`#/${n.route}`} aria-current={route === n.route ? "page" : undefined}
              onClick={(e) => { e.preventDefault(); go(n.route); }}
              className={clsx("press shrink-0 rounded-sm px-2.5 py-1.5 text-sm font-medium", route === n.route ? "text-text" : "text-muted hover:text-text", route === "all" && "opacity-60")}>
              {n.label}
            </a>
          ))}
        </nav>
        <button type="button" onClick={() => setPaletteOpen(true)} aria-label="Search symbols"
          className="press ml-auto flex h-8 shrink-0 items-center gap-2 rounded-sm border border-line bg-surface px-2.5 text-sm text-faint hover:border-line-strong hover:text-muted md:ml-0 lg:w-56">
          <MagnifyingGlass size={16} />
          <span className="hidden flex-1 text-left lg:inline">Search</span>
          <kbd className="hidden rounded-[4px] border border-line px-1.5 font-sans text-xs text-faint lg:inline">{isMac ? "⌘K" : "Ctrl K"}</kbd>
        </button>
        <button type="button" className="btn-ghost h-8 w-8 shrink-0 px-0" aria-label={`Theme: ${theme}. Switch to ${nextTheme}`} title={`Theme: ${theme}`}
          onClick={() => saveSettings({ theme: nextTheme })}>
          <ThemeIcon size={18} />
        </button>
        <button type="button" className={clsx("btn-ghost h-8 w-8 shrink-0 px-0", route === "settings" && "text-text")} aria-label="Settings" onClick={() => go("settings")}>
          <Gear size={18} />
        </button>
      </div>
    </header>
  );
}
