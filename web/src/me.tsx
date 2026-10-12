// Who is signed in, asked once.
//
// The app used to decide it was signed in by finding a string in localStorage,
// which said nothing about whose string it was — there was only ever the one.
// Several people share a box now, and half the screens need to know which of
// them is reading: the nav says your name, Settings shows People only to an
// instance admin, a project's People panel offers the chair's verbs only to the
// chair. One question to the server at start, held here, answers all of them;
// each screen asking on its own would be a dozen requests and, within a release,
// a dozen slightly different answers.

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { ApiError, get, Me, post } from "./api";

interface MeState {
  /** `undefined` while the first answer is in flight, `null` when nobody is
      signed in. The two are different screens: one is a moment's blank, the
      other is the sign-in form, and flashing the form at a person who is in
      fact signed in reads as having been thrown out. */
  me: Me | null | undefined;
  /** Set when the question could not be asked at all — the server is down, a
      proxy is in the way. That is not "signed out", and must not look like it. */
  error: string | null;
  /** Ask again — after signing in, or after a change to your own account. */
  refresh: () => Promise<Me | null>;
  signOut: () => Promise<void>;
}

const MeContext = createContext<MeState | null>(null);

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const who = await get<Me>("/auth/me");
      setMe(who);
      setError(null);
      return who;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setMe(null);
        setError(null);
        return null;
      }
      // Whatever we knew stays known: a blip on the network is not a reason to
      // take somebody's name off the screen they are working in.
      setError((e as Error).message);
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await post("/auth/logout");
    } catch {
      /* a session that is already gone is as signed out as this gets */
    }
    setMe(null);
    window.location.hash = "#/login";
  }, []);

  return (
    <MeContext.Provider value={{ me, error, refresh, signOut }}>{children}</MeContext.Provider>
  );
}

export function useMe(): MeState {
  const state = useContext(MeContext);
  if (!state) throw new Error("useMe() outside <MeProvider>");
  return state;
}
