import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch on mount and poll on an interval; exposes reload() for after-action refreshes.
    `at` is when the data last landed — a reader who reconciles against their own
    records needs to know how old a number is, and a stale-but-labelled number
    beats a blank screen. Data survives an error for exactly that reason. */
export function usePoll<T>(fn: () => Promise<T>, intervalMs = 5000): {
  data: T | null;
  error: string | null;
  at: Date | null;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [at, setAt] = useState<Date | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(() => {
    fnRef
      .current()
      .then((d) => {
        setData(d);
        setAt(new Date());
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    reload();
    const timer = setInterval(reload, intervalMs);
    return () => clearInterval(timer);
  }, [reload, intervalMs]);

  return { data, error, at, reload };
}

export function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
}
