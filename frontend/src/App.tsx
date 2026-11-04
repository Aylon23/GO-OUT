import { CommandPalette } from "./components/CommandPalette";
import { QuickView } from "./components/QuickView";
import { TopBar } from "./components/TopBar";
import AllPortfolios from "./pages/AllPortfolios";
import Calls from "./pages/Calls";
import Compare from "./pages/Compare";
import Dashboard from "./pages/Dashboard";
import Holdings from "./pages/Holdings";
import Risk from "./pages/Risk";
import Settings from "./pages/Settings";
import Transactions from "./pages/Transactions";
import WhatIf from "./pages/Scenarios";
import Council from "./pages/Council";
import { AppProvider, useApp } from "./state";

function Routes() {
  const { route } = useApp();
  switch (route) {
    case "all": return <AllPortfolios />;
    case "holdings": return <Holdings />;
    case "risk": return <Risk />;
    case "compare": return <Compare />;
    case "calls": return <Calls />;
    case "transactions": return <Transactions />;
    case "whatif": return <WhatIf />;
    case "council": return <Council />;
    case "settings": return <Settings />;
    default: return <Dashboard />;
  }
}

/** Re-keys on route change so each page enters with the same short rise. */
function RouteFrame() {
  const { route } = useApp();
  return <div key={route} className="route-in"><Routes /></div>;
}

export default function App() {
  return (
    <AppProvider>
      <div className="min-h-[100dvh] bg-bg text-text">
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[70] focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2 focus:shadow-pop">Skip to content</a>
        <TopBar />
        <main id="main"><RouteFrame /></main>
        <CommandPalette />
        <QuickView />
      </div>
    </AppProvider>
  );
}
