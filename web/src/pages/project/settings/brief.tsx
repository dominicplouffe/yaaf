// Settings → Brief: what every agent reads first, on every run. The goal, the
// standing orders, and the orders taken back — each order a line of its own,
// moved, edited, retired or restored in place, saved once from the bar.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Dashboard, patch, post } from "../../../api";
import { fmtWhen } from "../../../hooks";
import { Icon } from "../../../ui/Icon";
import { Group, PendingBar, SectionHead } from "../../../settings/kit";

interface GoalGrade {
  elements: { element: string; verdict: "present" | "weak" | "missing"; note: string }[];
  summary: string;
  ready: boolean;
}

const same = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);
const clean = (list: string[]) => list.map((l) => l.trim()).filter(Boolean);

export function BriefSection({ projectId, dash, onSaved, onError }: {
  projectId: string;
  dash: Dashboard;
  onSaved: (note: string) => void;
  onError: (message: string) => void;
}) {
  const savedOrders = dash.standing_orders ?? [];
  const savedRetired = dash.retired_orders ?? [];
  const [goal, setGoal] = useState(dash.goal);
  const [orders, setOrders] = useState<string[]>(savedOrders);
  const [retired, setRetired] = useState<string[]>(savedRetired);
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);

  // A save, or a change made in chat, moves the brief under the page; the
  // boxes follow it unless something here is still unsaved.
  const key = `${dash.goal}\u0000${savedOrders.join("\n")}\u0000${savedRetired.join("\n")}`;
  const [base, setBase] = useState({ key, goal: dash.goal, orders: savedOrders, retired: savedRetired });
  const goalDirty = goal.trim() !== dash.goal;
  useEffect(() => {
    if (key === base.key) return;
    // Untouched since the last version the page held: take the new one.
    const untouched = goal.trim() === base.goal && same(clean(orders), base.orders)
      && same(clean(retired), base.retired);
    setBase({ key, goal: dash.goal, orders: savedOrders, retired: savedRetired });
    if (untouched) { setGoal(dash.goal); setOrders(savedOrders); setRetired(savedRetired); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const count = (goalDirty ? 1 : 0) + countListChanges(savedOrders, clean(orders))
    + countListChanges(savedRetired, clean(retired));
  const short = goal.trim().length < 10;

  const move = (i: number, by: number) => setOrders((list) => {
    const next = [...list];
    const j = i + by;
    if (j < 0 || j >= next.length) return list;
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });
  const retire = (i: number) => { setRetired((r) => [orders[i], ...r]); setOrders((o) => o.filter((_, k) => k !== i)); };
  const restore = (i: number) => { setOrders((o) => [...o, retired[i]]); setRetired((r) => r.filter((_, k) => k !== i)); };

  async function save() {
    setBusy(true);
    try {
      await patch(`/admin/projects/${projectId}`, {
        goal: goal.trim(), standing_orders: clean(orders), retired_orders: clean(retired),
      });
      onSaved("Brief saved. Every agent reads it from its next run; the CEO has been told to bring its files in line.");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SectionHead title="Brief"
        lede="What every agent reads first, on every run. A change is in force from each agent's next run, and the CEO is told to bring its files and checks in line."
        actions={
          <Link className="btnlink" to={`/projects/${projectId}/chat`}
            state={{ draft: "I want to change direction: " }}
            title="The CEO drafts the change with what it means for the plan; nothing moves until you confirm it in your Inbox.">
            Ask the CEO to redraft it</Link>
        }>
        {dash.brief_confirmed_at
          ? <span className="badge good">Confirmed {fmtWhen(dash.brief_confirmed_at)}</span>
          : <span className="badge">Not confirmed yet</span>}
      </SectionHead>

      <Group title="Goal" aside={<span className="help">what done looks like</span>}>
        <div className="set-card">
          <textarea id="brief-goal" className="pset-goal" aria-label="The goal" value={goal}
            onChange={(e) => setGoal(e.target.value)} />
          <GoalFoot projectId={projectId} length={goal.trim().length} short={short} />
        </div>
      </Group>

      <Group title="Standing orders" count={orders.length || undefined}
        aside={<span className="help">rules the CEO must follow, numbered as agents see them</span>}>
        <div className="set-card">
          <ol className="pset-orders">
            {orders.length === 0 && <li className="empty">No standing orders.</li>}
            {orders.map((text, i) => (
              <li key={i}>
                <span className="n">{i + 1}.</span>
                <input value={text} aria-label={`Order ${i + 1}`}
                  className={i >= savedOrders.length || savedOrders[i] !== text ? "changed" : ""}
                  onChange={(e) => setOrders((o) => o.map((x, k) => (k === i ? e.target.value : x)))} />
                <span className="acts">
                  <button type="button" className="icon small flip" aria-label={`Move order ${i + 1} up`}
                    disabled={i === 0} onClick={() => move(i, -1)}><Icon name="down" size={14} /></button>
                  <button type="button" className="icon small" aria-label={`Move order ${i + 1} down`}
                    disabled={i === orders.length - 1} onClick={() => move(i, 1)}><Icon name="down" size={14} /></button>
                  <button type="button" className="small" onClick={() => retire(i)}>Retire</button>
                  <button type="button" className="icon small" aria-label={`Delete order ${i + 1}`}
                    onClick={() => setOrders((o) => o.filter((_, k) => k !== i))}><Icon name="x" size={14} /></button>
                </span>
              </li>
            ))}
          </ol>
          <form className="pset-add" onSubmit={(e) => {
            e.preventDefault();
            if (!adding.trim()) return;
            setOrders((o) => [...o, adding.trim()]);
            setAdding("");
          }}>
            <input value={adding} aria-label="New standing order"
              placeholder="Add an order, e.g. No feature ships without an accessibility pass"
              onChange={(e) => setAdding(e.target.value)} />
            <button type="submit" className="small" disabled={!adding.trim()}>Add</button>
          </form>
        </div>
      </Group>

      <Group title="No longer applies" aside={<span className="help">orders you took back; agents are told to stop following them</span>}>
        <div className="set-card">
          <ol className="pset-orders retired">
            {retired.length === 0 && <li className="empty">Nothing taken back.</li>}
            {retired.map((text, i) => (
              <li key={i}>
                <span className="n" />
                <span className="txt">{text}</span>
                <span className="acts">
                  <button type="button" className="small" onClick={() => restore(i)}>Restore</button>
                  <button type="button" className="icon small" aria-label="Delete"
                    onClick={() => setRetired((r) => r.filter((_, k) => k !== i))}><Icon name="x" size={14} /></button>
                </span>
              </li>
            ))}
          </ol>
        </div>
      </Group>

      <PendingBar count={count} what="to the brief" note="Every agent reads it from its next run."
        saveLabel="Save the brief" busy={busy} blocked={short ? "The goal needs at least 10 characters" : null}
        onDiscard={() => { setGoal(dash.goal); setOrders(savedOrders); setRetired(savedRetired); }}
        onSave={save} />
    </>
  );
}

/** How many lines differ, roughly as a reader would count them. */
function countListChanges(before: string[], after: string[]): number {
  if (same(before, after)) return 0;
  let n = Math.abs(before.length - after.length);
  for (let i = 0; i < Math.min(before.length, after.length); i++) if (before[i] !== after[i]) n++;
  return Math.max(1, n);
}

/** The goal's length, and the check against the goal-writing rules the
    Founder uses today. A project keeps the goal it was founded with; the rules
    have moved since some were made. One model call, and it changes nothing. */
function GoalFoot({ projectId, length, short }: { projectId: string; length: number; short: boolean }) {
  const [grade, setGrade] = useState<GoalGrade | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gaps = (grade?.elements ?? []).filter((e) => e.verdict !== "present");
  return (
    <>
      <div className="pset-goal-foot">
        <span className={short ? "warn-ink" : ""}>{length.toLocaleString()} characters</span>
        <span className="grow" />
        <button type="button" className="small" disabled={busy} onClick={async () => {
          setBusy(true); setError(null);
          try {
            setGrade(await post<GoalGrade>(`/admin/projects/${projectId}/regrade-goal`, {}));
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}>{busy ? "Checking…" : "Check against today's goal rules"}</button>
        <span>One model call. Changes nothing.</span>
      </div>
      {error && <div className="pset-goal-result"><span className="error">{error}</span></div>}
      {grade && (
        <div className="pset-goal-result">
          <b>{gaps.length === 0 ? `Nothing missing. ${grade.summary}` : grade.summary}</b>
          {gaps.length > 0 && (
            <ul>
              {gaps.map((e) => (
                <li key={e.element}>
                  <b>{e.element.replace(/_/g, " ")}</b>{e.verdict === "weak" ? " is weak" : " is missing"}: {e.note}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
