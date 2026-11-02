import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { dateLong, rangeStartDate, today } from "../lib/format";
import { useApp } from "../state";
import { Segmented } from "./ui";

const CODES = ["1M", "3M", "YTD", "1Y", "3Y", "5Y", "MAX"] as const;

/** `trackingSince`: ranges that would start before real tracking began are disabled. */
export function RangeSelector({ trackingSince }: { trackingSince?: string }) {
  const { range, setRange } = useApp();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState(range.start ?? "");
  const [end, setEnd] = useState(range.end ?? today());
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);
  const tooEarly = (c: string) => { const st = rangeStartDate(c); return !!(trackingSince && st && st < trackingSince); };
  const tip = trackingSince ? `Tracking started ${dateLong(trackingSince)}` : undefined;
  const opts = [...CODES.map((c) => ({ value: c as string, label: c, disabled: tooEarly(c), title: tooEarly(c) ? tip : undefined })),
    { value: "CUSTOM", label: "Custom" }];
  const shown = tooEarly(range.range) ? "MAX" : range.range;
  const err = start && end && start > end ? "Start must be before end." : "";
  return (
    <div ref={ref} className="relative">
      <Segmented label="Date range" accent size="sm" options={opts} value={shown}
        onChange={(v) => { if (v === "CUSTOM") setOpen(true); else { setOpen(false); setRange({ range: v }); } }} />
      {open && (
        <form className="pop-in absolute right-0 top-full z-30 mt-1.5 w-[280px] rounded-md bg-surface p-4 shadow-pop"
          onSubmit={(e) => { e.preventDefault(); if (!start || err) return; setRange({ range: "CUSTOM", start, end }); setOpen(false); }}
          onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label" htmlFor="rs-start">From</label><input id="rs-start" type="date" className="field px-2 text-sm" value={start} max={today()} onChange={(e) => setStart(e.target.value)} autoFocus /></div>
            <div><label className="label" htmlFor="rs-end">To</label><input id="rs-end" type="date" className="field px-2 text-sm" value={end} max={today()} onChange={(e) => setEnd(e.target.value)} /></div>
          </div>
          {err && <p className="error-text">{err}</p>}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button type="submit" className={clsx("btn-primary", (!start || err) && "opacity-50")} disabled={!start || !!err}>Apply</button>
          </div>
        </form>
      )}
    </div>
  );
}
