import { Info, X } from "@phosphor-icons/react";
import clsx from "clsx";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useMediaQuery } from "../lib/hooks";

export function Segmented<T extends string>({ options, value, onChange, label, accent = false, size = "md" }: {
  options: { value: T; label: string; disabled?: boolean; title?: string }[]; value: T; onChange: (v: T) => void; label: string; accent?: boolean; size?: "sm" | "md";
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const idx = options.findIndex((o) => o.value === value);
  const onKey = (e: React.KeyboardEvent) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    let n = idx;
    for (let k = 0; k < options.length; k++) {  // skip disabled options
      n = (n + d + options.length) % options.length;
      if (!options[n].disabled) break;
    }
    onChange(options[n].value);
    refs.current[n]?.focus();
  };
  return (
    <div role="radiogroup" aria-label={label} onKeyDown={onKey} className="inline-flex max-w-full flex-wrap rounded-sm bg-sunken p-0.5">
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button key={o.value} ref={(el) => { refs.current[i] = el; }} type="button" role="radio" aria-checked={on} tabIndex={on ? 0 : -1}
            onClick={() => { if (!o.disabled) onChange(o.value); }} aria-disabled={o.disabled || undefined} title={o.title}
            className={clsx("press shrink-0 whitespace-nowrap rounded-[5px] font-medium", size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-sm",
              o.disabled ? "cursor-not-allowed text-faint opacity-50" :
              on ? clsx("bg-surface shadow-[0_1px_2px_var(--line)]", accent ? "text-accent" : "text-text") : "text-muted hover:text-text")}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function InfoTip({ text, children, align = "auto" }: { text: string; children?: ReactNode; align?: "left" | "right" | "auto" }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  const [side, setSide] = useState<"left" | "right">("left");
  useEffect(() => {
    if (!open || align !== "auto" || !ref.current) return;
    setSide(ref.current.getBoundingClientRect().left > window.innerWidth / 2 ? "right" : "left");
  }, [open, align]);
  const s = align === "auto" ? side : align;
  return (
    <span ref={ref} className="relative inline-flex items-center gap-1" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      {children}
      <button type="button" aria-describedby={id} aria-label="What does this mean?" onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
        onClick={() => setOpen((o) => !o)} className="rounded-full text-faint hover:text-muted">
        <Info size={14} />
      </button>
      {open ? (
        <span id={id} role="tooltip"
          className={clsx("fade-in pointer-events-none absolute top-full z-40 mt-1.5 w-max max-w-[min(260px,80vw)] rounded-sm bg-surface px-3 py-2 text-left text-xs font-normal normal-case leading-4 text-text shadow-pop",
            s === "right" ? "right-0" : "left-0")}>
          {text}
        </span>
      ) : <span id={id} className="sr-only">{text}</span>}
    </span>
  );
}

/** Right side panel on desktop, bottom sheet below 768px. Not a modal dialog for content flow, but traps Esc. */
export function SidePanel({ open, onClose, title, children, wide = false }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const mobile = useMediaQuery("(max-width: 767px)");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => { window.removeEventListener("keydown", h); prev?.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      {mobile && <div className="fade-in fixed inset-0 z-40 bg-scrim" onClick={onClose} aria-hidden />}
      <aside ref={ref} tabIndex={-1} role="complementary" aria-label={title}
        className={clsx("fixed z-50 flex flex-col bg-surface shadow-panel outline-none",
          mobile ? "sheet-in inset-x-0 bottom-0 max-h-[88dvh] rounded-t-lg"
            : clsx("panel-in bottom-3 right-3 top-[68px] rounded-lg", wide ? "w-[520px]" : "w-[440px]"))}>
        {mobile && <div className="mx-auto mt-2 h-1 w-9 rounded-full bg-line-strong" aria-hidden />}
        <div className="flex items-center justify-between gap-3 px-5 pb-3 pt-4">
          <h2 className="truncate text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close panel" className="btn-ghost h-8 w-8 px-0"><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-y-auto overscroll-contain px-5 pb-6">{children}</div>
      </aside>
    </>
  );
}

export function EmptyState({ title, body, children }: { title: string; body?: string; children?: ReactNode }) {
  return (
    <div className="fade-in flex flex-col items-start gap-2 py-12">
      <p className="text-base font-medium">{title}</p>
      {body && <p className="max-w-[52ch] text-sm text-muted">{body}</p>}
      {children && <div className="mt-2 flex flex-wrap items-center gap-3">{children}</div>}
    </div>
  );
}

export function ErrorLine({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 py-3 text-sm text-loss">
      <span>{message}</span>
      {onRetry && <button type="button" className="link text-sm" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Skel({ className }: { className?: string }) {
  return <div aria-hidden className={clsx("skeleton", className)} />;
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <h2 className="text-lg font-semibold">{children}</h2>
      {aside}
    </div>
  );
}

export function Signed({ value, children, className }: { value: number | null | undefined; children: ReactNode; className?: string }) {
  const c = value === null || value === undefined || !isFinite(value) || Math.abs(value) < 5e-5 ? "text-text" : value > 0 ? "text-gain" : "text-loss";
  return <span className={clsx("num", c, className)}>{children}</span>;
}
