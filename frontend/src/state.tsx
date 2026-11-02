import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { setDisplayCurrency } from "./lib/format";
import { api, type PortfolioInfo, type RangeParams, setApiPortfolio, type Settings, type SymbolItem } from "./lib/api";
import { invalidate } from "./lib/hooks";

export type Route = "all" | "dashboard" | "holdings" | "risk" | "compare" | "calls" | "transactions" | "whatif" | "settings";
const ROUTES: Route[] = ["all", "dashboard", "holdings", "risk", "compare", "calls", "transactions", "whatif", "settings"];

const readPortfolio = (): number => {
  try {
    const n = Number(localStorage.getItem("portfolio"));
    if (n > 0) return n;
  } catch { /* ignore */ }
  return 1;
};
// set before the first request goes out
setApiPortfolio(readPortfolio());

interface AppState {
  route: Route;
  go: (r: Route) => void;
  range: RangeParams;
  setRange: (r: RangeParams) => void;
  settings: Settings | null;
  saveSettings: (s: Partial<Settings>) => Promise<void>;
  quickView: SymbolItem | null;
  openQuickView: (s: SymbolItem | null) => void;
  paletteOpen: boolean;
  setPaletteOpen: (o: boolean) => void;
  addTxFor: SymbolItem | null | undefined; // undefined = closed, null = open with no symbol
  startAddTx: (s?: SymbolItem | null) => void;
  closeAddTx: () => void;
  refresh: () => void;
  portfolios: PortfolioInfo[];
  portfolioId: number;
  portfolio: PortfolioInfo | null;
  setPortfolio: (id: number) => void;
  reloadPortfolios: () => Promise<PortfolioInfo[]>;
}

const Ctx = createContext<AppState | null>(null);
export const useApp = () => {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp outside provider");
  return c;
};

const readRoute = (): Route => {
  const h = window.location.hash.replace(/^#\/?/, "") as Route;
  return ROUTES.includes(h) ? h : "dashboard";
};
const readRange = (): RangeParams => {
  try {
    const r = JSON.parse(localStorage.getItem("range") ?? "");
    if (r && typeof r.range === "string") return r;
  } catch { /* ignore */ }
  return { range: "1Y" };
};

export function applyTheme(theme: string) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
  try { localStorage.setItem("theme", theme); } catch { /* ignore */ }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<Route>(readRoute);
  const [range, setRangeState] = useState<RangeParams>(readRange);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [quickView, setQuickView] = useState<SymbolItem | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [addTxFor, setAddTxFor] = useState<SymbolItem | null | undefined>(undefined);
  const [portfolios, setPortfolios] = useState<PortfolioInfo[]>([]);
  const [portfolioId, setPortfolioId] = useState<number>(readPortfolio);

  const setPortfolio = useCallback((id: number) => {
    setApiPortfolio(id);
    setPortfolioId(id);
    setAddTxFor(undefined);
    try { localStorage.setItem("portfolio", String(id)); } catch { /* ignore */ }
    invalidate();
  }, []);
  const reloadPortfolios = useCallback(async () => {
    const list = await api.portfolios();
    setPortfolios(list);
    // the remembered portfolio may have been deleted
    if (list.length && !list.some((p) => p.id === readPortfolio())) setPortfolio(list[0].id);
    return list;
  }, [setPortfolio]);
  useEffect(() => { reloadPortfolios().catch(() => {}); }, [reloadPortfolios]);

  useEffect(() => {
    const h = () => setRoute(readRoute());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  useEffect(() => {
    api.settings().then((s) => { setDisplayCurrency(s.base_currency); setSettings(s); applyTheme(s.theme); }).catch(() => {});
  }, []);

  const go = useCallback((r: Route) => { window.location.hash = `/${r}`; setRoute(r); window.scrollTo({ top: 0 }); }, []);
  const setRange = useCallback((r: RangeParams) => {
    setRangeState(r);
    try { localStorage.setItem("range", JSON.stringify(r)); } catch { /* ignore */ }
  }, []);
  const saveSettings = useCallback(async (s: Partial<Settings>) => {
    const next = await api.saveSettings(s);
    setDisplayCurrency(next.base_currency);
    setSettings(next);
    applyTheme(next.theme);
    invalidate();
  }, []);
  const startAddTx = useCallback((s?: SymbolItem | null) => {
    setQuickView(null);
    setAddTxFor(s ?? null);
    go("transactions");
  }, [go]);

  return (
    <Ctx.Provider value={{
      route, go, range, setRange, settings, saveSettings, quickView, openQuickView: setQuickView, paletteOpen, setPaletteOpen,
      addTxFor, startAddTx, closeAddTx: () => setAddTxFor(undefined), refresh: invalidate,
      portfolios, portfolioId, portfolio: portfolios.find((p) => p.id === portfolioId) ?? null, setPortfolio, reloadPortfolios,
    }}>
      {children}
    </Ctx.Provider>
  );
}
