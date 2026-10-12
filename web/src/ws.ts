// Live-update WebSocket client.
//
// This is a LATENCY HINT layered over polling — exactly like LISTEN/NOTIFY
// over the dispatcher's polling floor on the server side. The usePoll timers
// stay untouched and remain the source of truth; the socket only makes "poll
// now" happen sooner. If it can't connect, drops, or gives up entirely,
// nothing breaks — the UI is just up to one poll interval staler.
//
// The server pushes an opaque JSON "change stamp" whenever the project's data
// fingerprint changes. We never inspect it: any message means onChange().

import { useEffect, useRef } from "react";

const BACKOFF_MIN_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
// After this many consecutive failed connections, stop trying — silently.
// Polling covers it, and a dead socket must never surface as an error.
const MAX_FAILURES = 8;

/**
 * Open a live-update socket for a project and call onChange on every stamp
 * message. Reconnects with capped exponential backoff; gives up quietly after
 * repeated failures. Browsers can't set headers on a WebSocket, which is why
 * the token used to ride in the URL — where proxies log it. The session cookie
 * rides on the upgrade request like on any other same-origin request, so the
 * URL carries nothing, and a signed-out browser is simply refused and backs
 * off like any other failure.
 */
export function useProjectLive(projectId: string, onChange: () => void): void {
  // Ref so a fresh onChange closure never tears down the socket.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!projectId) return;
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let failures = 0;
    let unmounted = false;

    const connect = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const url = `${scheme}://${location.host}/ws/projects/${projectId}`;
      let sawStamp = false;
      try {
        socket = new WebSocket(url);
      } catch {
        return; // malformed URL / blocked — polling covers it
      }
      socket.onmessage = () => {
        // A stamp arriving proves the connection is good; reset the backoff.
        sawStamp = true;
        failures = 0;
        onChangeRef.current();
      };
      socket.onclose = () => {
        if (unmounted) return;
        // A close before any stamp (e.g. no session, dead endpoint) counts as
        // a failure; enough of those and we go silent for good.
        if (!sawStamp) failures += 1;
        if (failures > MAX_FAILURES) return;
        const delay = Math.min(
          BACKOFF_MAX_MS,
          BACKOFF_MIN_MS * 2 ** Math.max(0, failures - 1),
        );
        timer = window.setTimeout(connect, delay);
      };
      // onclose fires after onerror; no separate handling needed, but keep
      // errors from ever reaching the console as unhandled.
      socket.onerror = () => {};
    };

    connect();
    return () => {
      unmounted = true;
      if (timer !== undefined) window.clearTimeout(timer);
      socket?.close();
    };
  }, [projectId]);
}
