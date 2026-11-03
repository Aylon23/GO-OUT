import { Warning } from "@phosphor-icons/react";
import { useState } from "react";
import { api, ApiError, type BackupItem, type BackupStatus } from "../lib/api";
import { dateLong } from "../lib/format";
import { useData } from "../lib/hooks";
import { useApp } from "../state";
import { ErrorLine, Skel } from "./ui";

const when = (iso: string) => {
  const d = new Date(iso);
  return `${dateLong(iso.slice(0, 10))}, ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
};
const size = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);

/** Settings → Data: last backup, where they go, back up now, and restore with a safety copy first. */
export function Backups() {
  const q = useData(api.backups, "backups");
  const [s, setS] = useState<BackupStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [restored, setRestored] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const d = s ?? q.data;
  if (q.error && !d) return <ErrorLine message={q.error.message} onRetry={q.reload} />;
  if (!d) return <Skel className="h-24 w-full" />;
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); } catch (e) { setErr(e instanceof ApiError ? e.message : "That didn't work. Try again."); } finally { setBusy(false); }
  };
  const backUp = () => run(async () => {
    const r = await api.backupNow();
    setS(r);
    setMsg(`Backed up ${r.made.rows.toLocaleString()} rows to ${r.made.folders.length === 1 ? "this computer" : `${r.made.folders.length} folders`}.` +
      (r.made.errors.length ? ` Not copied: ${r.made.errors.join("; ")}` : ""));
  });
  const restore = (stem: string) => run(async () => {
    await api.restore(stem);
    setConfirm(null); setRestored(stem);
    setS(await api.backups());
    setMsg(null);
  });
  const undo = () => run(async () => { await api.undoRestore(); window.location.reload(); });
  const list = showAll ? d.backups : d.backups.slice(0, 8);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" disabled={busy} onClick={backUp}>{busy ? "Working…" : "Back up now"}</button>
        <p className="text-sm">
          {d.last_backup ? <>Last backup {when(d.last_backup)}</> : "No backup yet."}
          {d.warn && d.last_backup && <span className="ml-2 inline-flex items-center gap-1 text-loss"><Warning size={14} weight="fill" />over {Math.floor(d.days_since ?? 0)} days ago</span>}
        </p>
      </div>
      {msg && <p className="text-sm text-muted" role="status">{msg}</p>}
      {restored && (
        <p className="flex flex-wrap items-center gap-2 text-sm" role="status">
          Restored the backup from {when(d.backups.find((b) => b.stem === restored)?.at ?? new Date().toISOString())}. The data before it was saved as a safety copy.
          <button type="button" className="btn-secondary h-7 text-xs" disabled={busy} onClick={undo}>Undo restore</button>
          <button type="button" className="btn-ghost h-7 text-xs" onClick={() => window.location.reload()}>Reload the app</button>
        </p>
      )}
      {err && <ErrorLine message={err} />}

      <div className="text-sm">
        <p className="font-medium">Where backups go</p>
        <p className="mt-1 text-xs text-muted">Every day at the first start, and whenever you back up. Each one is a database copy and an Excel file with one sheet per table. The newest of each of the last {d.keep.days} days and {d.keep.months} months are kept.</p>
        <ul className="mt-2 space-y-1">
          {d.locations.map((l) => (
            <li key={l.path} className="break-all text-xs"><span className="text-muted">{l.kind === "local" ? "This computer: " : "Second folder: "}</span>{l.path}
              {l.error ? <span className="text-loss"> · {l.error}</span> : <span className="text-faint"> · {l.count} backups</span>}</li>
          ))}
        </ul>
        <form className="mt-3 flex max-w-[560px] gap-2" onSubmit={(e) => { e.preventDefault(); run(async () => { setS(await api.backupFolder(folder ?? "")); setFolder(null); setMsg(folder ? "Second folder saved. The next backup goes there too." : "Second folder removed."); }); }}>
          <input aria-label="Second backup folder" className="field h-9 flex-1 text-sm" spellCheck={false} dir="auto"
            placeholder="Second folder, e.g. your synced Google Drive folder" value={folder ?? d.extra_dir} onChange={(e) => setFolder(e.target.value)} />
          <button type="submit" className="btn-secondary" disabled={busy || folder === null}>Save folder</button>
        </form>
      </div>

      {d.backups.length > 0 && (
        <div>
          <p className="text-sm font-medium">Backups</p>
          <ul className="mt-2 divide-y divide-line">{list.map((b) => <Row key={b.stem} b={b} busy={busy} confirming={confirm === b.stem}
            onAsk={() => setConfirm(b.stem)} onCancel={() => setConfirm(null)} onRestore={() => restore(b.stem)} />)}</ul>
          {d.backups.length > 8 && <button type="button" className="btn-ghost mt-1 h-7 text-xs" onClick={() => setShowAll((v) => !v)}>{showAll ? "Show fewer" : `Show all ${d.backups.length}`}</button>}
        </div>
      )}
    </div>
  );
}

function Row({ b, busy, confirming, onAsk, onCancel, onRestore }: { b: BackupItem; busy: boolean; confirming: boolean; onAsk: () => void; onCancel: () => void; onRestore: () => void }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
      <span>{when(b.at)}<span className="text-xs text-faint"> · {size(b.bytes)}{b.has_excel ? " · with Excel" : ""}{b.safety ? " · safety copy before a restore" : ""}{b.in_extra ? " · also in the second folder" : ""}</span></span>
      {confirming ? (
        <span className="flex items-center gap-2">
          <span className="text-xs">Replace all current data with this backup? A safety copy is made first.</span>
          <button type="button" className="btn-ghost h-7 text-xs" onClick={onCancel}>Keep current data</button>
          <button type="button" className="btn-secondary h-7 text-xs text-loss" disabled={busy} onClick={onRestore}>{busy ? "Restoring…" : "Restore"}</button>
        </span>
      ) : <button type="button" className="btn-ghost h-7 text-xs" onClick={onAsk}>Restore</button>}
    </li>
  );
}

/** Home: a warning when the last backup is 3 or more days old. */
export function BackupWarning() {
  const q = useData(api.backups, "backups");
  const { go } = useApp();
  const d = q.data;
  if (!d || !d.warn) return null;
  return (
    <p className="mb-6 flex flex-wrap items-center gap-2 rounded-sm border border-loss px-3 py-2 text-sm" role="alert">
      <Warning size={16} weight="fill" className="text-loss" />
      {d.last_backup ? `No backup for ${Math.floor(d.days_since ?? 0)} days.` : "No backup yet."}
      <button type="button" className="btn-ghost h-7 text-xs" onClick={() => go("settings")}>Open backups</button>
    </p>
  );
}
