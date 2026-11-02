import { FilePdf, FileText, ImageSquare } from "@phosphor-icons/react";
import { useState } from "react";
import { api, ApiError, download, type ActionsBody, type Memo as M } from "../lib/api";
import { useApp } from "../state";
import { ErrorLine, Segmented, Skel } from "./ui";

/** Analyst memo for the scenario on screen, built from the engine's numbers only. */
export function MemoPanel({ body }: { body: ActionsBody }) {
  const { settings } = useApp();
  const [lang, setLang] = useState<"he" | "en">(settings?.memo_language ?? "he");
  const [m, setM] = useState<M | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const go = async (what: "view" | "pdf" | "card") => {
    setBusy(what); setErr(null);
    try {
      if (what === "view") setM(await api.memo({ ...body, lang }));
      else await download(what === "pdf" ? "/api/memo/pdf" : "/api/memo/card", { ...body, lang }, what === "pdf" ? "memo.pdf" : "memo.png");
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't make the memo."); } finally { setBusy(null); }
  };
  return (
    <section className="mt-12" aria-label="Analyst memo">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-2 text-lg font-semibold">Analyst memo</h2>
        <Segmented label="Memo language" size="sm" value={lang} onChange={(v) => { setLang(v); setM(null); }} options={[{ value: "he", label: "עברית" }, { value: "en", label: "English" }]} />
        <button type="button" className="btn-secondary h-8" disabled={!!busy} onClick={() => go("view")}><FileText size={14} />{busy === "view" ? "Writing" : "Show"}</button>
        <button type="button" className="btn-ghost h-8" disabled={!!busy} onClick={() => go("pdf")}><FilePdf size={14} />{busy === "pdf" ? "Making PDF" : "PDF"}</button>
        <button type="button" className="btn-ghost h-8" disabled={!!busy} onClick={() => go("card")}><ImageSquare size={14} />{busy === "card" ? "Drawing" : "Image card"}</button>
      </div>
      <p className="mt-1 text-xs text-muted">Nine short sections, written only from the numbers on this page and the scenario's own research.</p>
      {err && <ErrorLine message={err} />}
      {busy === "view" && !m && <div className="mt-4 space-y-2"><Skel className="h-5 w-2/3" /><Skel className="h-32 w-full" /></div>}
      {m && (
        <article dir={m.lang === "he" ? "rtl" : "ltr"} className="mt-5 max-w-[760px] rounded-md border border-line bg-surface p-5 text-sm leading-6">
          <h3 className="text-lg font-semibold">{m.title}</h3>
          {m.sections.map((s) => (
            <div key={s.n} className="mt-4">
              <p className="font-semibold text-accent">{s.n}. {s.title}</p>
              <ul className="mt-1 list-disc space-y-1 ps-5">{s.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </div>
          ))}
        </article>
      )}
    </section>
  );
}
