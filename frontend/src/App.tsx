import { CommandPalette } from "./components/CommandPalette";
import { QuickView } from "./components/QuickView";
import { TopBar } from "./components/TopBar";
import AllPortfolios from "./pages/AllPortfolios";
import Dashboard from "./pages/Dashboard";
import Holdings from "./pages/Holdings";
import Risk from "./pages/Risk";
import Settings from "./pages/Settings";
import Transactions from "./pages/Transactions";
import WhatIf from "./pages/WhatIf";
import { AppProvider, useApp } from "./state";

function Routes() {
  const { route } = useApp();
  switch (route) {
    case "all": return <AllPortfolios />;
    case "holdings": return <Holdings />;
    case "risk": return <Risk />;
    case "transactions": return <Transactions />;
    case "whatif": return <WhatIf />;
    case "settings": return <Settings />;
    default: return <Dashboard />;
  }
}

export default function App() {
  return (
    <AppProvider>
      <div className="min-h-[100dvh] bg-bg text-text">
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[70] focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2 focus:shadow-pop">Skip to content</a>
        <TopBar />
        <main id="main"><Routes /></main>
        <CommandPalette />
        <QuickView />
      </div>
    </AppProvider>
  );
}
