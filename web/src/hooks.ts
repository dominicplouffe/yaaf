import { useCallback, useEffect, useRef, useState } from "react";

const STUCK_MS = 60_000;

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
  // Requests are numbered so an answer can never overwrite a newer one: a slow
  // reply to the question before the last click is not news. `pending` lets a
  // timer tick stand down while a request is still out — against a server
  // slower than the interval, ticks used to stack until the tab stopped
  // answering, which is how a slow screen became a dead one.
  const issued = useRef(0);
  const landed = useRef(0);
  const pending = useRef(0);
  const outSince = useRef(0);

  const reload = useCallback(() => {
    const mine = ++issued.current;
    if (pending.current++ === 0) outSince.current = Date.now();
    fnRef
      .current()
      .then((d) => {
        if (mine < landed.current) return;
        landed.current = mine;
        setData(d);
        setAt(new Date());
        setError(null);
      })
      .catch((e: Error) => {
        if (mine < landed.current) return;
        setError(e.message);
      })
      .finally(() => {
        if (--pending.current === 0) outSince.current = 0;
      });
  }, []);

  // A single string, because the effect's dependency list has to be the same
  // length on every render and a caller's array is not.
  const depKey = JSON.stringify(deps);
  useEffect(() => {
    reload();
    const timer = setInterval(() => {
      // A request out for a minute is not coming back; standing down for it
      // for ever would freeze the screen as surely as stacking did.
      if (pending.current === 0 || Date.now() - outSince.current > STUCK_MS) reload();
    }, intervalMs);
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
