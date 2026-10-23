/** The offer that follows a repeated yes. Approving the same kind of spend
    for the third time returns an offer to stop asking (yaaf.server.policy.
    spend_precedent); the row that was decided is gone by then, so the offer
    is announced app-wide and shown by the one host the shell mounts. Taking
    it is an ordinary rule change, recorded with its reason. */
import { useEffect, useState } from "react";
import { post } from "./api";
import { ConfirmModal } from "./ui";

export interface SpendOffer {
  project_id: string;
  changes: Record<string, number>;
  question: string;
  reason: string;
  keeps: string;
}

const EVENT = "yaaf:spend-offer";
const DECLINED = "yaaf.precedent.declined";

/** Keyed by project and the lines on offer: saying no to $150 is not saying
    no to $250 after a larger yes. */
function declineKey(offer: SpendOffer): string {
  return `${offer.project_id}:${JSON.stringify(offer.changes)}`;
}

function declined(): string[] {
  try {
    return JSON.parse(localStorage.getItem(DECLINED) ?? "[]");
  } catch {
    return [];
  }
}

export function announceOffer(offer: SpendOffer | undefined) {
  if (!offer || declined().includes(declineKey(offer))) return;
  window.dispatchEvent(new CustomEvent<SpendOffer>(EVENT, { detail: offer }));
}

export function OfferHost() {
  const [offer, setOffer] = useState<SpendOffer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const on = (e: Event) => { setError(""); setOffer((e as CustomEvent<SpendOffer>).detail); };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  if (!offer) return null;

  const no = () => {
    try {
      localStorage.setItem(DECLINED, JSON.stringify([...declined(), declineKey(offer)]));
    } catch { /* the offer simply comes back next time */ }
    setOffer(null);
  };
  const yes = async () => {
    setBusy(true);
    try {
      await post(`/admin/projects/${offer.project_id}/amendments`,
        { reason: offer.reason, changes: offer.changes });
      setOffer(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <ConfirmModal title="Stop asking you about purchases like this?" confirmLabel="Yes, stop asking"
      cancelLabel="Keep asking me"
      busy={busy} onConfirm={yes} onCancel={no}>
      <p>{offer.question}</p>
      <p className="help">{offer.keeps} You can lower the line again in Settings → Rules in force.</p>
      {error && <div className="error">{error}</div>}
    </ConfirmModal>
  );
}
