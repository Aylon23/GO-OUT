import { CaretUpDown, Check, Plus, SquaresFour } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { useApp } from "../state";

/** Pick which portfolio every page shows, open the all-portfolios view, or add a portfolio. */
export function PortfolioSwitcher() {
  const { portfolios, portfolioId, portfolio, setPortfolio, reloadPortfolios, go, route } = useApp();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) close(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);
  const close = () => { setOpen(false); setAdding(false); setName(""); setErr(null); };

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      const p = await api.createPortfolio(name);
      await reloadPortfolios();
      setPortfolio(p.id);
      close();
      go("transactions");
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Couldn't create it."); }
  }

  const label = route === "all" ? "All portfolios" : portfolio?.name ?? "Portfolio";
  return (
    <div ref={ref} className="relative shrink-0">
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        className="press flex h-8 max-w-[180px] items-center gap-1.5 rounded-sm px-2 text-sm font-medium hover:bg-hover">
        <span className="truncate">{label}</span>
        <CaretUpDown size={14} className="shrink-0 text-faint" />
      </button>
      {open && (
        <div role="menu" onKeyDown={(e) => { if (e.key === "Escape") close(); }}
          className="pop-in absolute left-0 top-full z-40 mt-1.5 w-64 rounded-md bg-surface py-1.5 shadow-pop">
          <button type="button" role="menuitem" onClick={() => { close(); go("all"); }}
            className={clsx("mx-1.5 flex w-[calc(100%-12px)] items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-sm hover:bg-hover", route === "all" && "font-medium")}>
            <SquaresFour size={16} className="text-muted" /> All portfolios
          </button>
          <div className="my-1.5 border-t border-line" />
          {portfolios.map((p) => (
            <button key={p.id} type="button" role="menuitemradio" aria-checked={p.id === portfolioId && route !== "all"}
              onClick={() => { setPortfolio(p.id); close(); if (route === "all" || route === "settings") go("dashboard"); }}
              className="mx-1.5 flex w-[calc(100%-12px)] items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-sm hover:bg-hover">
              <span className="w-4 shrink-0 text-accent">{p.id === portfolioId && route !== "all" && <Check size={14} />}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate">{p.name}</span>
                {p.note && <span className="block truncate text-xs text-faint">{p.note}</span>}
              </span>
              {p.transactions === 0 && <span className="text-xs text-faint">Empty</span>}
            </button>
          ))}
          <div className="my-1.5 border-t border-line" />
          {adding ? (
            <form onSubmit={create} className="px-3 pb-1.5 pt-1">
              <label htmlFor="new-pf" className="label">Name</label>
              <input id="new-pf" autoFocus className="field h-8 text-sm" value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Dad" maxLength={60} />
              {err && <p className="error-text">{err}</p>}
              <div className="mt-2.5 flex justify-end gap-2">
                <button type="button" className="btn-ghost h-8" onClick={() => setAdding(false)}>Cancel</button>
                <button type="submit" className="btn-primary h-8" disabled={!name.trim()}>Create</button>
              </div>
            </form>
          ) : (
            <button type="button" role="menuitem" onClick={() => setAdding(true)}
              className="mx-1.5 flex w-[calc(100%-12px)] items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-sm text-muted hover:bg-hover hover:text-text">
              <Plus size={16} /> New portfolio
            </button>
          )}
        </div>
      )}
    </div>
  );
}
