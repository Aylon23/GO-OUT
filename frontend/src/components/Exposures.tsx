import { CaretDown, CaretRight } from "@phosphor-icons/react";
import clsx from "clsx";
import { Fragment, useState } from "react";
import { api, type ExposureBook, type ExposureFactor } from "../lib/api";
import { dateLong, pct } from "../lib/format";
import { useData, useMediaQuery } from "../lib/hooks";
import { Avatar } from "./Avatar";
import { ErrorLine, InfoTip, Skel } from "./ui";

const MAIN = ["market", "rates", "oil", "dollar", "shekel"];
const MORE = ["size", "value", "momentum", "quality", "gold"];

const beta = (v: number | undefined) => (v == null || !isFinite(v) ? "–" : (v < 0 ? "−" : "") + Math.abs(v).toFixed(2));

function sentence(f: ExposureFactor, b: ExposureBook, family: boolean) {
  const effect = (b.betas[f.id] ?? 0) * f.shock;
  const who = family ? "the family's money" : `${b.name}'s portfolio`;
  if (Math.abs(effect) < 0.0025) return `${f.when}, ${who} barely moves.`;
  return `${f.when}, ${who} ${effect < 0 ? "falls" : "rises"} about ${pct(Math.abs(effect), Math.abs(effect) < 0.02 ? 1 : 0, false)} (in shekels).`;
}

export function Exposures() {
  const q = useData(() => api.exposures(), "exposures");
  const wide = useMediaQuery("(min-width: 1024px)");
  const [open, setOpen] = useState<number | null | "none">("none");
  const [selected, setSelected] = useState(0);
  const d = q.data;
  if (q.error && !d) return <section className="mt-16"><h2 className="text-lg font-semibold">Exposures</h2><ErrorLine message={q.error.message} onRetry={q.reload} /></section>;
  if (!d) return <section className="mt-16" aria-hidden><Skel className="h-6 w-40" /><Skel className="mt-4 h-40 w-full" /></section>;
  if (!d.books.length) return null;
  const cols = (wide ? [...MAIN, ...MORE] : MAIN).map((id) => d.factors.find((f) => f.id === id)!).filter(Boolean);
  const sel = d.books[Math.min(selected, d.books.length - 1)];
  const isFamily = (b: ExposureBook) => b === d.books[0] && d.books.length > 1;
  return (
    <section className="mt-16" aria-label="Exposures">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Exposures</h2>
          <p className="mt-1 max-w-[70ch] text-sm text-muted">What moves each portfolio, in shekels. Betas from up to {Math.round(d.window_weeks / 52)} years of weekly returns, as of {dateLong(d.as_of)}. Click a row for its holdings.</p>
        </div>
      </div>
      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[560px] border-separate border-spacing-0 text-sm">
          <thead><tr>
            <th scope="col" className="border-b border-line py-2 text-left text-xs font-medium text-muted">Portfolio</th>
            {cols.map((f) => (
              <th key={f.id} scope="col" className="border-b border-line py-2 pl-3 text-right text-xs font-medium text-muted">
                <span className="inline-flex items-center justify-end gap-1">{f.label}<InfoTip text={f.tip} /></span>
              </th>
            ))}
          </tr></thead>
          <tbody>
            {d.books.map((b, i) => {
              const expanded = open === b.id;
              return (
                <Fragment key={b.id ?? "family"}>
                  <tr tabIndex={0} aria-expanded={expanded} onClick={() => { setSelected(i); setOpen(expanded ? "none" : b.id); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { setSelected(i); setOpen(expanded ? "none" : b.id); } }}
                    className={clsx("cursor-pointer transition-colors duration-fast hover:bg-hover", i === selected && "bg-hover")}>
                    <td className={clsx("border-b border-line py-2.5", isFamily(b) && "font-semibold")}>
                      <span className="inline-flex items-center gap-1.5">{expanded ? <CaretDown size={12} /> : <CaretRight size={12} className="text-faint" />}{b.name}</span>
                    </td>
                    {cols.map((f) => <td key={f.id} className="num border-b border-line py-2.5 pl-3 text-right">{beta(b.betas[f.id])}</td>)}
                  </tr>
                  {expanded && b.holdings.map((h) => (
                    <tr key={h.symbol} className="bg-sunken/40 text-xs">
                      <td className="border-b border-line py-2 pl-5">
                        <span className="flex items-center gap-2">
                          <Avatar ticker={h.symbol} size={20} />
                          <span className="min-w-0">
                            <span className="font-mono font-semibold">{h.symbol}</span>
                            <span className="ml-1.5 text-faint">{pct(h.weight, 1, false)}</span>
                            <span className="block text-faint">
                              R² {h.r2 == null ? "–" : h.r2.toFixed(2)} · residual vol {h.resid_vol == null ? "–" : pct(h.resid_vol, 0, false)} · {h.weeks} wk
                              {h.low_confidence && <span className="ml-1.5 font-medium text-text">low confidence</span>}
                              {h.short_history && <span className="ml-1.5 font-medium text-text">short history</span>}
                            </span>
                          </span>
                        </span>
                      </td>
                      {cols.map((f) => <td key={f.id} className={clsx("num border-b border-line py-2 pl-3 text-right", h.low_confidence && "text-muted")}>{beta(h.betas[f.id])}</td>)}
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-faint">
        R² is how much of a holding's weekly moves the factors explain; under {d.low_r2} the betas are marked low confidence. Holdings with under a year of history lean on their sector ETF.
        Cash in dollars counts toward USD/ILS.
      </p>
      <div className="mt-6">
        <h3 className="text-sm font-medium">{isFamily(sel) ? "The family" : sel.name}, in plain words</h3>
        <ul className="mt-2 grid gap-x-8 gap-y-1.5 text-sm text-muted md:grid-cols-2">
          {d.factors.filter((f) => MAIN.includes(f.id) || Math.abs((sel.betas[f.id] ?? 0) * f.shock) >= 0.005).map((f) => (
            <li key={f.id}>{sentence(f, sel, isFamily(sel))}</li>
          ))}
        </ul>
        <SectorTilt b={sel} names={d.sectors} />
      </div>
    </section>
  );
}

function SectorTilt({ b, names }: { b: ExposureBook; names: Record<string, string> }) {
  const tilts = Object.entries(b.betas).filter(([k, v]) => k.startsWith("sector_") && Math.abs(v) >= 0.05).sort((a, c) => Math.abs(c[1]) - Math.abs(a[1]));
  if (!tilts.length) return null;
  return (
    <p className="mt-3 text-xs text-faint">
      Sector tilts beyond the market: {tilts.map(([k, v], i) => <span key={k}>{i > 0 && ", "}{names[k] ?? k} {beta(v)}</span>)}.
    </p>
  );
}
