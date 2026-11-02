import { Check, Copy } from "@phosphor-icons/react";
import { useState } from "react";
import { api } from "../lib/api";
import { useData } from "../lib/hooks";
import { ErrorLine, Segmented, SidePanel, Skel } from "./ui";

/** A short summary in Hebrew, ready to paste into WhatsApp for the person whose portfolio it is. */
export function ReportPanel({ all, title, onClose }: { all: boolean; title: string; onClose: () => void }) {
  const [period, setPeriod] = useState("week");
  const q = useData(() => api.report(period, all), `rep-${all}-${period}`);
  const [copied, setCopied] = useState(false);
  return (
    <SidePanel open title={title} onClose={onClose}>
      <Segmented label="Period" size="sm" value={period} onChange={setPeriod}
        options={[{ value: "week", label: "Week" }, { value: "month", label: "Month" }, { value: "year", label: "Year" }]} />
      <div className="mt-5">
        {q.error ? <ErrorLine message={q.error.message} onRetry={q.reload} /> : !q.data ? <div className="space-y-2"><Skel className="h-4 w-3/4" /><Skel className="h-4 w-2/3" /><Skel className="h-4 w-1/2" /></div> : (
          <pre dir="rtl" className="whitespace-pre-wrap rounded-md bg-sunken p-4 font-sans text-sm leading-6">{q.data.text}</pre>
        )}
      </div>
      <button type="button" className="btn-primary mt-4 w-full" disabled={!q.data}
        onClick={async () => { if (!q.data) return; try { await navigator.clipboard.writeText(q.data.text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ } }}>
        {copied ? <><Check size={16} /> Copied</> : <><Copy size={16} /> Copy to send</>}
      </button>
      <p className="mt-2 text-xs text-faint">The Telegram bot can send this automatically every Sunday (Settings).</p>
    </SidePanel>
  );
}
