import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

/** Tiny data hook: refetches when `key` changes; global `invalidate()` refreshes every mounted hook. */
const listeners = new Set<() => void>();
export const invalidate = () => listeners.forEach((l) => l());

export function useData<T>(fn: () => Promise<T>, key: string, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(enabled);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const load = useCallback(() => {
    if (!enabled) return;
    const n = ++seq.current;
    setLoading(true);
    fnRef.current()
      .then((d) => { if (n === seq.current) { setData(d); setError(null); } })
      .catch((e) => { if (n === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, String(e))); })
      .finally(() => { if (n === seq.current) setLoading(false); });
  }, [enabled]);
  useEffect(load, [key, load]);
  useEffect(() => { listeners.add(load); return () => { listeners.delete(load); }; }, [load]);
  return { data, error, loading, reload: load };
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
