import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch on mount and poll on an interval; exposes reload() for after-action refreshes.
    `at` is when the data last landed — a reader who reconciles against their own
    records needs to know how old a number is, and a stale-but-labelled number
    beats a blank screen. Data survives an error for exactly that reason.

    `deps` are the values `fn` reads that the caller can change — a chosen
    window, a selected channel. The fetcher itself lives in a ref, so without
    them a click would only take effect on the next tick: the button lights up
    and the numbers below it keep answering the question you just stopped
    asking. Naming them here re-fetches at once and restarts the clock. */
export function usePoll<T>(fn: () => Promise<T>, intervalMs = 5000, deps: unknown[] = []): {
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

  // A single string, because the effect's dependency list has to be the same
  // length on every render and a caller's array is not.
  const depKey = JSON.stringify(deps);
  useEffect(() => {
    reload();
    const timer = setInterval(reload, intervalMs);
    return () => clearInterval(timer);
  }, [reload, intervalMs, depKey]);

  return { data, error, at, reload };
}

/** Built once. `toLocaleString` builds one of these per call, and the tables
    and chat logs here call it per row per render — a few hundred formatters a
    second on a busy page, for four fields that never change. */
const WHEN = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  return WHEN.format(new Date(iso));
}

export function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
}
