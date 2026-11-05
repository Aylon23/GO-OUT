import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, cachedAnswer, captureUrls } from "./api";

/** Tiny data hook: refetches when `key` changes; global `invalidate()` refreshes every mounted hook. */
const listeners = new Set<() => void>();
export const invalidate = () => listeners.forEach((l) => l());

/** Fetches on mount and when `key` changes. If the same request was answered before (this session or
 *  an earlier visit), that answer is painted at once and replaced when the fresh one arrives
 *  (stale-while-revalidate). `updatedAt` is when the data on screen was fetched; `stale` is true while a
 *  cached answer is showing. */
export function useData<T>(fn: () => Promise<T>, key: string, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [stale, setStale] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const load = useCallback(() => {
    if (!enabled) return;
    const n = ++seq.current;
    setLoading(true);
    const { result, urls } = captureUrls(() => fnRef.current());
    const hit = urls.length === 1 ? cachedAnswer(urls[0]) : undefined;
    if (hit) { setData(hit.data as T); setUpdatedAt(hit.at || null); setStale(true); setError(null); }
    result
      .then((d) => { if (n === seq.current) { setData(d); setError(null); setUpdatedAt(Date.now()); setStale(false); } })
      .catch((e) => { if (n === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, String(e))); })
      .finally(() => { if (n === seq.current) setLoading(false); });
  }, [enabled]);
  useEffect(load, [key, load]);
  useEffect(() => { listeners.add(load); return () => { listeners.delete(load); }; }, [load]);
  return { data, error, loading, reload: load, updatedAt, stale };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

export function useMediaQuery(q: string) {
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const h = () => setM(mq.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, [q]);
  return m;
}
