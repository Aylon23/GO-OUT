import { MagnifyingGlass, X } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api, ApiError, type Quote, type SymbolItem } from "../lib/api";
import { money, pct, signClass } from "../lib/format";
import { splitMatch } from "../lib/highlight";
import { useDebounced } from "../lib/hooks";
import { Avatar } from "./Avatar";

interface Props {
  onSelect: (item: SymbolItem) => void;
  placeholder?: string;
  autoFocus?: boolean;
  /** "popover": results float under the input (forms). "list": results render inline (command palette, hero). */
  variant?: "popover" | "list";
  size?: "md" | "lg";
  exclude?: string[];
  label?: string;
  id?: string;
  onEscape?: () => void;
  invalid?: boolean;
}

interface Section { title: string | null; items: SymbolItem[] }

export function SymbolSearch({ onSelect, placeholder = "Search by ticker or company", autoFocus, variant = "popover", size = "md", exclude = [], label, id, onEscape, invalid }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(variant === "list");
  const [active, setActive] = useState(0);
  const [results, setResults] = useState<SymbolItem[] | null>(null);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [recent, setRecent] = useState<SymbolItem[]>([]);
  const [popular, setPopular] = useState<SymbolItem[]>([]);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [selectErr, setSelectErr] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const debounced = useDebounced(query, 250);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const inputId = id ?? `${listId}-input`;

  // empty state data
  useEffect(() => {
    if (!open) return;
    api.recent().then(setRecent).catch(() => setRecent([]));
    api.popular().then(setPopular).catch(() => setPopular([]));
  }, [open]);

  // search (results from the first character)
  useEffect(() => {
    const q = debounced.trim();
    if (!q) { setResults(null); setLoading(false); setSearchErr(null); return; }
    const ctl = new AbortController();
    setLoading(true);
    api.search(q, ctl.signal)
      .then((r) => { setResults(r.results); setSearchErr(r.error ?? null); setActive(0); })
      .catch((e) => { if (e.name !== "AbortError") { setResults([]); setSearchErr(e instanceof ApiError ? e.message : "Search failed."); } })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    return () => ctl.abort();
  }, [debounced]);

  const typingAhead = query.trim() !== debounced.trim();
  const showSkeleton = query.trim() !== "" && (loading || typingAhead) && results === null;

  const sections: Section[] = useMemo(() => {
    const ex = new Set(exclude.map((e) => e.toUpperCase()));
    const f = (l: SymbolItem[]) => l.filter((i) => !ex.has(i.symbol));
    if (query.trim()) return [{ title: null, items: f(results ?? []) }];
    const r = f(recent).slice(0, 5);
    const rs = new Set(r.map((x) => x.symbol));
    const out: Section[] = [];
    if (r.length) out.push({ title: "Recent", items: r });
    out.push({ title: "Popular", items: f(popular).filter((p) => !rs.has(p.symbol)) });
    return out;
  }, [query, results, recent, popular, exclude]);
  const flat = sections.flatMap((s) => s.items);

  // quotes for visible rows
  const symbolsKey = flat.filter((i) => !i.raw).map((i) => i.symbol).join(",");
  useEffect(() => {
    if (!symbolsKey) return;
    const need = symbolsKey.split(",").filter((s) => !quotes[s]);
    if (!need.length) return;
    let live = true;
    api.quotes(need).then((q) => { if (live) setQuotes((old) => ({ ...old, ...q })); }).catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey]);

  // click outside closes (popover only)
  useEffect(() => {
    if (variant !== "popover" || !open) return;
    const h = (e: MouseEvent) => { if (!rootRef.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open, variant]);

  useEffect(() => { if (active >= flat.length) setActive(Math.max(0, flat.length - 1)); }, [flat.length, active]);

  async function choose(item: SymbolItem) {
    setSelectErr(null);
    setSelecting(true);
    try {
      const meta = await api.select(item);
      onSelect({ ...item, ...meta });
      setQuery("");
      setResults(null);
      if (variant === "popover") setOpen(false);
    } catch (e) {
      setSelectErr(e instanceof ApiError ? e.message : "Couldn't select that symbol.");
    } finally {
      setSelecting(false);
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); const it = flat[active]; if (it && open) choose(it); }
    else if (e.key === "Escape") {
      if (variant === "popover" && open) { e.stopPropagation(); setOpen(false); }
      else onEscape?.();
    }
  }

  useEffect(() => {
    document.getElementById(`${listId}-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [active, listId]);

  const lg = size === "lg";
  const list = (
    <div
      id={listId}
      role="listbox"
      aria-label="Symbols"
      className={clsx(
        "overflow-y-auto overscroll-contain",
        variant === "popover" ? "pop-in absolute left-0 right-0 top-full z-30 mt-1.5 max-h-[min(420px,60dvh)] rounded-md bg-surface py-1.5 shadow-pop" : "max-h-[min(460px,60dvh)] py-1.5",
      )}
    >
      {showSkeleton && <SkeletonRows />}
      {!showSkeleton && query.trim() !== "" && flat.length === 0 && (
        <div className="px-4 py-6 text-sm text-muted">
          No results for “{query.trim()}”. Try the exact ticker, like <span className="font-mono text-text">AAPL</span> or <span className="font-mono text-text">TEVA.TA</span>.
        </div>
      )}
      {!showSkeleton && sections.map((sec, si) => sec.items.length > 0 && (
        <div key={si} role="group" aria-label={sec.title ?? "Results"}>
          {sec.title && <div className="px-4 pb-1 pt-2.5 text-xs font-medium text-faint">{sec.title}</div>}
          {sec.items.map((it) => {
            const idx = flat.indexOf(it);
            return (
              <Row key={it.symbol} item={it} query={query} quote={quotes[it.symbol]} active={idx === active}
                id={`${listId}-opt-${idx}`} onHover={() => setActive(idx)} onClick={() => choose(it)} />
            );
          })}
        </div>
      ))}
      {searchErr && !showSkeleton && <div className="px-4 pb-2 pt-1 text-xs text-muted">{searchErr}</div>}
    </div>
  );

  return (
    <div ref={rootRef} className="relative">
      {label && <label htmlFor={inputId} className="label">{label}</label>}
      <div className={clsx("relative flex items-center", variant === "list" && "border-b border-line")}>
        <MagnifyingGlass size={lg ? 20 : 16} className={clsx("pointer-events-none absolute text-faint", lg ? "left-4" : "left-3")} />
        <input
          ref={inputRef}
          id={inputId}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={open && flat[active] ? `${listId}-opt-${active}` : undefined}
          aria-autocomplete="list"
          aria-invalid={invalid || undefined}
          autoFocus={autoFocus}
          autoComplete="off"
          spellCheck={false}
          value={query}
          placeholder={placeholder}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); setResults(null); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKey}
          className={clsx(
            "w-full bg-transparent outline-none placeholder:text-faint",
            variant === "list"
              ? clsx("border-0", lg ? "h-14 pl-12 pr-10 text-lg" : "h-12 pl-10 pr-10 text-base")
              : clsx("field", lg ? "h-14 pl-12 text-lg" : "pl-9", invalid && "border-loss"),
          )}
        />
        {query && (
          <button type="button" aria-label="Clear search" onClick={() => { setQuery(""); inputRef.current?.focus(); }}
            className="press absolute right-2 rounded-sm p-1.5 text-faint hover:bg-hover hover:text-text">
            <X size={14} />
          </button>
        )}
      </div>
      {selectErr && <p className="error-text px-1" role="alert">{selectErr}</p>}
      {selecting && <span className="sr-only">Loading symbol</span>}
      {open && list}
    </div>
  );
}

function Row({ item, query, quote, active, id, onHover, onClick }: {
  item: SymbolItem; query: string; quote?: Quote; active: boolean; id: string; onHover: () => void; onClick: () => void;
}) {
  const meta = [item.exchange, item.type].filter(Boolean).join(" · ");
  return (
    <div
      id={id}
      role="option"
      aria-selected={active}
      onMouseMove={onHover}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={clsx("mx-1.5 flex cursor-pointer items-center gap-3 rounded-sm px-2.5 py-2", active && "bg-hover")}
    >
      <Avatar ticker={item.symbol} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-semibold text-text"><Hl text={item.symbol} q={query} /></span>
          {meta && <span className="truncate text-xs text-faint">{meta}</span>}
        </div>
        <div className="truncate text-sm text-muted"><Hl text={item.name} q={query} /></div>
      </div>
      {!item.raw && (
        <div className="num shrink-0 text-right">
          {quote ? (
            <>
              <div className="text-sm text-text">{money(quote.price, quote.currency, { decimals: 2 })}</div>
              <div className={clsx("text-xs", signClass(quote.change_pct))}>{pct(quote.change_pct)}</div>
            </>
          ) : (
            <div className="space-y-1.5"><div className="skeleton ml-auto h-3 w-14" /><div className="skeleton ml-auto h-2.5 w-10" /></div>
          )}
        </div>
      )}
    </div>
  );
}

function Hl({ text, q }: { text: string; q: string }) {
  return <>{splitMatch(text, q).map((p, i) => (p.match ? <b key={i} className="font-semibold text-text">{p.text}</b> : <span key={i}>{p.text}</span>))}</>;
}

function SkeletonRows() {
  return (
    <div aria-hidden>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="mx-1.5 flex items-center gap-3 px-2.5 py-2">
          <div className="skeleton h-7 w-7" />
          <div className="flex-1 space-y-1.5"><div className="skeleton h-3.5 w-16" /><div className="skeleton h-3 w-40 max-w-full" /></div>
          <div className="space-y-1.5"><div className="skeleton h-3 w-14" /><div className="skeleton ml-auto h-2.5 w-10" /></div>
        </div>
      ))}
    </div>
  );
}
