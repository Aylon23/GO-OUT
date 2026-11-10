import { useEffect, useState } from "react";
import { api, type AppVersion } from "../lib/api";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "10 Nov 2026, 17:57" in the computer's own time. */
const when = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

/** A thin line under the header: which version is running, and whether it is the latest. */
export function VersionLine() {
  const [v, setV] = useState<AppVersion | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => api.version().then((x) => { if (live) setV(x); }).catch(() => {});
    load();
    const t = window.setInterval(load, 5 * 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);
  if (!v) return null;
  const r = v.running;
  const c = v.check;
  return (
    <div className="mx-auto flex max-w-content flex-wrap justify-end gap-x-3 px-4 pt-2 text-xs text-faint md:px-8" role="status">
      <span title={r.note || undefined}>Version of {when(r.date) || "unknown date"}{r.sha && <span className="num"> ({r.sha})</span>}</span>
      {v.restart_needed ? <span className="font-medium text-loss">A newer version is already on this computer. Close the app window and run start.bat again.</span>
        : c && c.ok === false ? <span className="font-medium text-loss">The last update check failed ({when(c.at)}). You may not have the latest version.</span>
        : c && c.ok && c.latest && r.sha && c.latest !== r.sha ? <span className="font-medium text-loss">A newer version exists ({c.latest}). Run start.bat again.</span>
        : c && c.ok ? <span>Latest version, checked {when(c.at)}</span>
        : <span>Started {when(v.started_at)}</span>}
    </div>
  );
}
