import { useEffect } from "react";
import { useApp } from "../state";
import { SymbolSearch } from "./SymbolSearch";

export function CommandPalette() {
  const { paletteOpen, setPaletteOpen, openQuickView } = useApp();
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaletteOpen(!paletteOpen); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [paletteOpen, setPaletteOpen]);
  if (!paletteOpen) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[12dvh]">
      <div className="fade-in absolute inset-0 bg-scrim" onClick={() => setPaletteOpen(false)} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label="Search symbols" className="pop-in relative w-full max-w-[600px] overflow-hidden rounded-md bg-surface shadow-panel">
        <SymbolSearch variant="list" size="lg" autoFocus placeholder="Search a stock, ETF or index"
          onEscape={() => setPaletteOpen(false)}
          onSelect={(s) => { setPaletteOpen(false); openQuickView(s); }} />
        <div className="flex gap-4 border-t border-line px-4 py-2 text-xs text-faint">
          <span><kbd className="font-sans">↑↓</kbd> move</span><span><kbd className="font-sans">Enter</kbd> open</span><span><kbd className="font-sans">Esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}
