import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, type RangeParams, type Settings, type SymbolItem } from "./lib/api";
import { invalidate } from "./lib/hooks";

export type Route = "dashboard" | "holdings" | "risk" | "transactions" | "whatif" | "settings";
const ROUTES: Route[] = ["dashboard", "holdings", "risk", "transactions", "whatif", "settings"];

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

  useEffect(() => {
    const h = () => setRoute(readRoute());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  useEffect(() => {
    api.settings().then((s) => { setSettings(s); applyTheme(s.theme); }).catch(() => {});
  }, []);

  const go = useCallback((r: Route) => { window.location.hash = `/${r}`; setRoute(r); window.scrollTo({ top: 0 }); }, []);
  const setRange = useCallback((r: RangeParams) => {
    setRangeState(r);
    try { localStorage.setItem("range", JSON.stringify(r)); } catch { /* ignore */ }
  }, []);
  const saveSettings = useCallback(async (s: Partial<Settings>) => {
    const next = await api.saveSettings(s);
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
    }}>
      {children}
    </Ctx.Provider>
  );
}
