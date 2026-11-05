import { Gear, MagnifyingGlass, Monitor, Moon, Sun } from "@phosphor-icons/react";
import clsx from "clsx";
import { useLayoutEffect, useRef, useState } from "react";
import { type Route, useApp } from "../state";
import { MarketChip } from "./MarketChip";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

const NAV: { route: Route; label: string }[] = [
  { route: "dashboard", label: "Overview" },
  { route: "holdings", label: "Holdings" },
  { route: "risk", label: "Risk" },
  { route: "compare", label: "Compare" },
  { route: "calls", label: "Calls" },
  { route: "transactions", label: "Trades" },
  { route: "whatif", label: "What-if" },
  { route: "council", label: "Council" },
];

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

export function TopBar() {
  const { route, go, setPaletteOpen, settings, saveSettings } = useApp();
  const theme = settings?.theme ?? "system";
  const nextTheme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const ThemeIcon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  const navRef = useRef<HTMLElement>(null);
  const [ind, setInd] = useState<{ x: number; y: number; w: number } | null>(null);
  // Slide a 2px rule under the active page. Transform only, so it never triggers layout.
  useLayoutEffect(() => {
    const place = () => {
      const a = navRef.current?.querySelector<HTMLElement>('[aria-current="page"]');
      setInd(a ? { x: a.offsetLeft + 10, y: a.offsetTop + a.offsetHeight + (window.innerWidth >= 1024 ? 10 : 2), w: Math.max(1, a.offsetWidth - 20) } : null);
    };
    place();
    window.addEventListener("resize", place);
    document.fonts?.ready.then(place);
    return () => window.removeEventListener("resize", place);
  }, [route]);
  return (
    <header className="glass sticky top-0 z-30 border-b border-line">
      <div className="mx-auto flex max-w-content flex-wrap items-center gap-x-2 px-4 md:px-8 lg:h-14 lg:flex-nowrap lg:gap-5">
        <a href="#/dashboard" onClick={(e) => { e.preventDefault(); go("dashboard"); }} className="flex h-14 shrink-0 items-center gap-2 rounded-sm font-semibold lg:h-auto">
          <svg width="26" height="20" viewBox="0 0 26 20" aria-hidden className="shrink-0">
            <line x1="1" y1="13.5" x2="25" y2="13.5" stroke="var(--faint)" strokeWidth="1.4" strokeDasharray="2 2.6" />
            <path d="M1.5 16l6-5.2 4.5 2.6L24.5 3" stroke="var(--accent)" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="hidden font-display text-[21px] font-medium tracking-[-0.015em] sm:inline lg:hidden xl:inline">Benchmark</span>
        </a>
        <span aria-hidden className="hidden h-5 border-l border-line sm:block lg:hidden xl:block" />
        <PortfolioSwitcher />
        <nav ref={navRef} aria-label="Pages" className="relative order-last -mx-2.5 flex w-[calc(100%+20px)] flex-wrap items-center pb-2 lg:order-none lg:mx-0 lg:w-auto lg:min-w-0 lg:flex-1 lg:flex-nowrap lg:pb-0">
          {NAV.map((n) => (
            <a key={n.route} href={`#/${n.route}`} aria-current={route === n.route ? "page" : undefined}
              onClick={(e) => { e.preventDefault(); go(n.route); }}
              className={clsx("press shrink-0 rounded-sm px-2.5 py-1.5 text-sm font-medium", route === n.route ? "text-text" : "text-muted hover:text-text", route === "all" && "opacity-60")}>
              {n.label}
            </a>
          ))}
          <span aria-hidden className="nav-indicator" style={{ opacity: ind ? 1 : 0, transform: ind ? `translate(${ind.x}px, ${ind.y}px) scaleX(${ind.w})` : undefined }} />
        </nav>
        <span className="ml-auto lg:ml-0"><MarketChip /></span>
        <button type="button" onClick={() => setPaletteOpen(true)} aria-label="Search symbols or add a trade"
          className="press flex h-8 shrink-0 items-center gap-2 rounded-sm border border-line bg-surface px-2.5 text-sm text-faint hover:border-line-strong hover:text-muted lg:ml-0">
          <MagnifyingGlass size={16} />
          <span className="sr-only">Search or add</span>
          <kbd className="hidden rounded-[4px] border border-line px-1.5 font-sans text-xs text-faint md:inline">{isMac ? "⌘K" : "Ctrl K"}</kbd>
        </button>
        <button type="button" className="btn-ghost hidden h-8 w-8 shrink-0 px-0 sm:inline-flex" aria-label={`Theme: ${theme}. Switch to ${nextTheme}`} title={`Theme: ${theme}`}
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
