import { useEffect, useState } from "react";
import { avatarBg } from "../lib/avatar";

/** Logos come from /api/logo/{symbol} (fetched once by the server, cached on this computer).
 *  Symbols without a logo keep the letter avatar, at the same size. */
const missing = new Set<string>();
const version: Record<string, number> = {};
const EVENT = "logo-updated";

/** Call after uploading or removing a logo so every avatar of that symbol reloads. */
export function refreshLogo(symbol: string) {
  const s = symbol.toUpperCase();
  missing.delete(s);
  version[s] = Date.now();
  window.dispatchEvent(new CustomEvent(EVENT, { detail: s }));
}

export function Avatar({ ticker, size = 28 }: { ticker: string; size?: number }) {
  const sym = ticker.toUpperCase();
  const [, force] = useState(0);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const h = (e: Event) => { if ((e as CustomEvent).detail === sym) { setLoaded(false); force((n) => n + 1); } };
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, [sym]);
  useEffect(() => setLoaded(false), [sym]);
  const letter = sym.replace(/^\^/, "").charAt(0) || "?";
  const showImg = !missing.has(sym);
  return (
    <span aria-hidden className="relative inline-flex shrink-0 select-none items-center justify-center overflow-hidden rounded-sm font-mono font-semibold text-text"
      style={{ width: size, height: size, background: loaded ? "white" : avatarBg(sym), fontSize: Math.round(size * 0.46) }}>
      {!loaded && letter}
      {showImg && (
        <img src={`/api/logo/${encodeURIComponent(sym)}${version[sym] ? `?v=${version[sym]}` : ""}`} alt="" loading="lazy" decoding="async"
          width={size} height={size} className="absolute inset-0 h-full w-full object-contain"
          style={{ opacity: loaded ? 1 : 0, padding: Math.max(1, Math.round(size * 0.08)) }}
          onLoad={() => setLoaded(true)} onError={() => { missing.add(sym); setLoaded(false); force((n) => n + 1); }} />
      )}
    </span>
  );
}
