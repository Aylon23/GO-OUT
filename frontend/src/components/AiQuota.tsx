import { ArrowClockwise } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { api, type AiQuota as Q } from "../lib/api";
import { useData } from "../lib/hooks";

/** "X of Y AI requests left today": shown in Settings → AI and on every AI screen. */
export function AiQuota({ className }: { className?: string }) {
  const q = useData(() => api.aiQuota(), "ai-quota");
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<Q | null>(null);
  const d = fresh ?? q.data;
  const refresh = async () => { setBusy(true); try { setFresh(await api.aiQuota(true)); } finally { setBusy(false); } };
  let text: string;
  let tone = "text-muted";
  if (!d) text = "Checking AI requests…";
  else if (!d.key_set) text = "AI is off: no OpenRouter key";
  else if (!d.known) text = d.error ? "AI requests left today: unknown (offline)" : "AI requests left today: unknown";
  else {
    text = `${d.remaining} of ${d.limit} AI requests left today`;
    if ((d.remaining ?? 0) === 0) tone = "text-loss";
  }
  return (
    <span className={clsx("inline-flex items-center gap-1.5 text-xs", tone, className)} aria-live="polite">
      <span className="num">{text}</span>
      {d?.key_set && (
        <button type="button" aria-label="Refresh AI requests left" title={d.as_of ? `Read from OpenRouter ${new Date(d.as_of).toLocaleTimeString()}` : "Read from OpenRouter"}
          className="btn-ghost h-6 w-6 px-0" disabled={busy} onClick={refresh}><ArrowClockwise size={12} className={busy ? "animate-spin" : ""} /></button>
      )}
    </span>
  );
}
