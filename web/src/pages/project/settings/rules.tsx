// Rules in force, and the money rules Settings → Money shows from the same
// state. One edit state for both: a rule changed on Money is the same pending
// change on Rules in force, counted on its tab, and saved through the one bar
// that asks for the reason. A rule editable in two places with two edit
// states is the thing this page was dug out of.

import { Fragment, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { AmendmentRow, ConstitutionField, Dashboard, get, post } from "../../../api";
import { fmtWhen, usePoll } from "../../../hooks";
import { ConfirmModal } from "../../../ui";
import { Icon } from "../../../ui/Icon";
import { Seg } from "../../../ui/kit";
import { choiceLabel, humanizeHelp, splitLead } from "../../../settingsCopy";
import { PendingBar, SectionHead, UnitInput } from "../../../settings/kit";
import type { FounderStatus, PresetCard } from "../../StartProject";

// ── units ────────────────────────────────────────────────────────────────────

/** How a rule's number is said and typed. The schema carries no unit, but the
    rule's key always does — `_usd`, `_ratio`, `_hours` — so it is read from
    there rather than kept as a second list of thirty keys to go stale. */
export interface Unit {
  prefix?: string;
  suffix?: string;
  /** Stored as a share (0.8), typed and said as a percent (80). */
  percent?: boolean;
  wide?: boolean;
}

export function unitOf(field: ConstitutionField): Unit {
  const k = field.key;
  if (field.type !== "integer" && field.type !== "number") return {};
  if (/usd/.test(k)) return { prefix: "$" };
  if (/_ratio|_rate$|ratio_min/.test(k)) return { suffix: "%", percent: true };
  if (/multiple$/.test(k)) return { suffix: "×" };
  if (/token/.test(k)) return { suffix: "tokens", wide: true };
  if (/_pct$|coverage/.test(k)) return { suffix: "%" };
  if (/_minutes$|^canary_minutes$/.test(k)) return { suffix: "min" };
  if (/_hours(_max)?$|_hours_/.test(k) || k === "drought_hours") return { suffix: "hours" };
  if (/_days$/.test(k)) return { suffix: "days" };
  if (/_s$/.test(k)) return { suffix: "sec" };
  if (/lines/.test(k)) return { suffix: "lines" };
  return {};
}

/** A stored value, said the way a person reads it: $400, 80%, 3×, On. */
export function sayRule(field: ConstitutionField | undefined, value: unknown): string {
  if (value === null || value === undefined || value === "") return "(blank)";
  if (!field) return String(value);
  if (field.type === "boolean") return value === true || value === "true" ? "On" : "Off";
  if (field.choices) return choiceLabel(String(value));
  if (field.type === "string") return String(value);
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const u = unitOf(field);
  if (u.percent) return `${Math.round(n * 1000) / 10}%`;
  const said = n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (u.prefix) return `${u.prefix}${said}`;
  if (u.suffix === "%" || u.suffix === "×") return `${said}${u.suffix}`;
  return u.suffix ? `${said} ${u.suffix}` : said;
}

/** The text in a box, for a stored value: a share shows as its percent. */
function toBox(field: ConstitutionField, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (unitOf(field).percent && value !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? String(Math.round(n * 1000) / 10) : String(value);
  }
  return String(value);
}

/** And back: what the box says, as the string the edit state keeps. */
function fromBox(field: ConstitutionField, raw: string): string {
  if (unitOf(field).percent && raw.trim() !== "") {
    const n = Number(raw);
    return Number.isFinite(n) ? String(Math.round(n * 10) / 1000) : raw;
  }
  return raw;
}

// ── which rules are asleep ───────────────────────────────────────────────────

/** A rule the platform is not reading on this project, and why. Only one
    budget cap is ever enforced — `policy.check_budget` picks it from the
    billing mode — and the cycle rhythm exists for Operate projects only.
    These stay reachable, folded under their tab, saying why they sleep: a
    rule you cannot see is a rule you cannot change. */
export function dormantReason(key: string, dash: Dashboard): string | null {
  if ((key === "monthly_usd_cap" || key === "project_usd_budget") && dash.billing_mode !== "api_key")
    return "Not enforced: this project bills by subscription, so budgets count tokens.";
  if ((key === "monthly_token_cap" || key === "project_token_budget") && dash.billing_mode === "api_key")
    return "Not enforced: this project bills by API key, so budgets count dollars.";
  if ((key === "cycle_frequency" || key === "cycle_day") && dash.shape !== "operate")
    return dash.mode === "code_factory"
      ? "Takes effect once the build is handed over to upkeep."
      : `Operate projects only; this one is in ${dash.mode} mode.`;
  return null;
}

/** The money rules filed on other tabs by their group. Settings → Money lists
    them with the budget, from the same edit state. */
export const MONEY_ELSEWHERE = [
  { key: "max_usd_per_run", theme: "Pace & brakes" },
  { key: "approval_threshold_usd", theme: "Autonomy" },
];

// ── the edit state ───────────────────────────────────────────────────────────

/** Every rule the constitution holds, what is being changed, and the reason.
    Values start at what is enforced now; saving proposes one amendment that
    carries only what changed. */
export function useRules(projectId: string, dash: Dashboard) {
  const cons = dash.constitution as Record<string, unknown>;
  const [schema, setSchema] = useState<ConstitutionField[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [json, setJson] = useState("{}");
  const [reason, setReason] = useState("");
  const [presets, setPresets] = useState<FounderStatus["presets"] | null>(null);

  useEffect(() => {
    get<ConstitutionField[]>("/admin/constitution/schema")
      .then((fields) => {
        setSchema(fields);
        setEdits(Object.fromEntries(fields.map((f) => [f.key, String(cons[f.key] ?? "")])));
      })
      .catch(() => {});
    // Re-seeding on every dash poll would fight the chairman's typing; the
    // constitution only moves when this page saves, and that reloads it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);
  // The same cards the new-project form offers, from the same endpoint.
  // Best-effort: the pickers simply do not render without them.
  useEffect(() => {
    get<FounderStatus>("/admin/founder/status").then((f) => setPresets(f.presets)).catch(() => {});
  }, []);
  const { data: amendments, reload: reloadAmendments } = usePoll(
    () => get<AmendmentRow[]>(`/admin/projects/${projectId}/amendments`), 10000);

  const changes = useMemo(() => changedRules(schema, edits, cons), [schema, edits, cons]);
  let jsonKeys: string[] = [];
  let jsonValues: Record<string, unknown> = {};
  const jsonText = json.trim() || "{}";
  try {
    jsonValues = JSON.parse(jsonText) as Record<string, unknown>;
    jsonKeys = Object.keys(jsonValues);
  } catch {
    jsonKeys = [];
  }
  const badJson = jsonText !== "{}" && jsonKeys.length === 0;
  const field = useCallback((key: string) => schema.find((f) => f.key === key), [schema]);
  const setRule = (key: string, raw: string) => setEdits((prev) => ({ ...prev, [key]: raw }));
  const discard = () => {
    setEdits(Object.fromEntries(schema.map((f) => [f.key, String(cons[f.key] ?? "")])));
    setJson("{}");
    setReason("");
  };
  const count = Object.keys(changes).length + jsonKeys.filter((k) => !(k in changes)).length + (badJson ? 1 : 0);
  return {
    schema, cons, edits, setEdits, setRule, changes, json, setJson, jsonKeys, jsonValues, badJson,
    reason, setReason, discard, count, field, presets, amendments, reloadAmendments,
  };
}
export type Rules = ReturnType<typeof useRules>;

function changedRules(
  fields: ConstitutionField[],
  edits: Record<string, string>,
  cons: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const raw = edits[f.key];
    if (raw === undefined || raw === String(cons[f.key] ?? "")) continue;
    if (f.type === "boolean") out[f.key] = raw === "true";
    else if (f.type === "string") out[f.key] = raw.trim() === "" ? null : raw.trim();
    else {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || raw.trim() === "") continue;
      out[f.key] = parsed;
    }
  }
  return out;
}

// ── one rule, one row ────────────────────────────────────────────────────────

function timezoneList(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  try {
    return intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    return [];
  }
}

/** The sentence on the left, the value in its unit on the right. A changed
    row says what it was and what it becomes; a rule off its default says so;
    the longer reasoning sits behind More, never a stack of Why? links. */
export function RuleRow({ field, rules, dormant, flash, tag }: {
  field: ConstitutionField;
  rules: Rules;
  dormant: string | null;
  flash?: boolean;
  /** The tab it lives on, when it is shown somewhere else (Money, a search). */
  tag?: string;
}) {
  const [more, setMore] = useState(false);
  const value = rules.edits[field.key] ?? "";
  const current = rules.cons[field.key];
  const changed = value !== String(current ?? "");
  const stock = field.default === null || field.default === undefined ? "" : String(field.default);
  const offStock = String(current ?? "") !== stock;
  const labelOf = (key: string) => rules.field(key)?.label;
  const [lead, rest] = splitLead(humanizeHelp(field.help, field.choices, labelOf), 8, 32,
                                 field.choices ? 40 : 22);
  const set = (raw: string) => rules.setRule(field.key, raw);
  const id = `rule-in-${field.key}`;
  const u = unitOf(field);

  let control: ReactNode;
  if (field.type === "boolean") {
    control = (
      <button type="button" role="switch" aria-checked={value === "true"} aria-label={field.label}
        className={`switch${value === "true" ? " on" : ""}`}
        onClick={() => set(value === "true" ? "false" : "true")} />
    );
  } else if (field.choices && field.choices.length <= 3) {
    control = <Seg value={value} onPick={set}
      options={field.choices.map((c) => ({ key: c, label: choiceLabel(c) }))} />;
  } else if (field.choices) {
    control = (
      <select id={id} value={value} aria-label={field.label} onChange={(e) => set(e.target.value)}>
        {field.choices.map((c) => <option key={c} value={c}>{choiceLabel(c)}</option>)}
      </select>
    );
  } else if (field.key === "timezone") {
    control = <>
      <input id={id} list="pset-tz" value={value} aria-label={field.label} onChange={(e) => set(e.target.value)} />
      <datalist id="pset-tz">{timezoneList().map((z) => <option key={z} value={z} />)}</datalist>
    </>;
  } else if (field.type === "string") {
    control = <input id={id} value={value} aria-label={field.label} onChange={(e) => set(e.target.value)} />;
  } else {
    control = <UnitInput id={id} label={field.label} value={toBox(field, value)}
      onChange={(raw) => set(fromBox(field, raw))} prefix={u.prefix} suffix={u.suffix} wide={u.wide}
      min={u.percent && field.minimum != null ? field.minimum * 100 : field.minimum}
      max={u.percent && field.maximum != null ? field.maximum * 100 : field.maximum}
      step={field.type === "integer" ? 1 : "any"} />;
  }

  return (
    <div id={`rule-${field.key}`}
      className={`prule${changed ? " dirty" : ""}${dormant ? " asleep" : ""}${flash ? " flash" : ""}`}>
      <div className="prule-text">
        <div className="prule-lab">
          <label htmlFor={id}>{field.label}</label>
          {offStock && !changed && <span className="badge accent">Changed from default</span>}
          {tag && <span className="prule-tag">{tag}</span>}
        </div>
        <p className="prule-help">{lead}</p>
        <div className="prule-meta">
          {changed && (
            <span className="prule-delta">{sayRule(field, current)} → {sayRule(field, value)}</span>
          )}
          {stock !== "" && <span>Default {sayRule(field, field.default)}</span>}
          {dormant && <span>{dormant}</span>}
          {rest && (
            <button type="button" className="linklike" aria-expanded={more} onClick={() => setMore(!more)}>
              {more ? "Less" : "More"}</button>
          )}
        </div>
        {more && rest && <p className="prule-more">{rest}</p>}
      </div>
      <div className="prule-ctl">{control}</div>
    </div>
  );
}

// ── the cards ────────────────────────────────────────────────────────────────

const asEdit = (value: unknown) => String(value ?? "");
const matches = (card: PresetCard, read: (key: string) => unknown) =>
  Object.entries(card.constitution).every(([k, v]) => asEdit(read(k)) === asEdit(v));

/** Re-pick a card on a running project. Picking stages the whole bundle into
    the same edit state a typed change uses, so it is saved as one amendment
    with one reason. A project tuned rule by rule is said to be hand-tuned and
    never rounded to the nearest card. */
function Presets({ title, cards, rules, theme }: {
  title: string; cards: PresetCard[]; rules: Rules; theme: string;
}) {
  if (!cards.length) return null;
  const staged = cards.find((c) => matches(c, (k) => rules.edits[k] ?? rules.cons[k]));
  const inForce = cards.find((c) => matches(c, (k) => rules.cons[k]));
  const elsewhere = staged && inForce && staged.value !== inForce.value
    ? Object.keys(staged.constitution).filter((k) => rules.field(k) && rules.field(k)!.theme !== theme).length
    : 0;
  return (
    <div className="pset-presets-wrap">
      <div className="set-group-h">{title}
        <span className="c"> · {inForce ? <>In force: {inForce.label}</> : "In force: hand-tuned"}. Picking a card stages all of its rules.</span>
      </div>
      <div className="pset-presets" role="radiogroup" aria-label={title}>
        {cards.map((card) => {
          const on = staged?.value === card.value;
          return (
            <button key={card.value} type="button" role="radio" aria-checked={on}
              className={`pset-preset${on ? " on" : ""}`}
              onClick={() => rules.setEdits((prev) => ({
                ...prev,
                ...Object.fromEntries(Object.entries(card.constitution).map(([k, v]) => [k, asEdit(v)])),
              }))}>
              <b><span className="radio" aria-hidden="true" />{card.label}</b>
              {card.headline && <span className="hl">{card.headline}</span>}
              <span className="bl">{card.blurb}</span>
              {inForce?.value === card.value && <span className="inforce">In force</span>}
            </button>
          );
        })}
      </div>
      {elsewhere > 0 && (
        <p className="help">{elsewhere} of {staged!.label}&apos;s rules live on other tabs; their changes count there.</p>
      )}
    </div>
  );
}

const PRESET_TABS: Record<string, { family: keyof FounderStatus["presets"]; title: string }> = {
  "Pace & brakes": { family: "tempo", title: "How often should it think?" },
  "The bar": { family: "bar", title: "How good does it have to be?" },
  "Autonomy": { family: "autonomy", title: "Who holds the verbs?" },
};

/** The four dials, read before the rulebook: how is this project set up? */
function Dials({ rules, usd, onOpen }: { rules: Rules; usd: boolean; onOpen: (theme: string) => void }) {
  const c = rules.cons;
  const p = rules.presets;
  const cap = Number(usd ? c.monthly_usd_cap : c.monthly_token_cap) || 0;
  const wall = cap * (Number(c.hard_stop_multiple) || 1);
  const job = Number(usd ? c.project_usd_budget : c.project_token_budget) || 0;
  const money = (n: number) => usd ? `$${Math.round(n).toLocaleString()}` : `${n.toLocaleString()} tokens`;
  const threshold = Number(c.approval_threshold_usd) || 0;
  const perPurchase = Number(c.max_usd_per_purchase) || 0;
  const asksAt = perPurchase > 0 && perPurchase < threshold ? perPurchase : threshold;
  const card = (family: keyof FounderStatus["presets"]) =>
    (p?.[family] ?? []).find((card) => matches(card, (k) => c[k]));
  const dial = (name: string, theme: string, family: keyof FounderStatus["presets"]) => {
    const found = card(family);
    return { name, theme, value: found ? found.label : "Hand-tuned",
      sub: found ? (found.headline ?? firstSentence(found.blurb)) : "set rule by rule" };
  };
  const dials = [
    { name: "Money", theme: "Money", value: job ? `${money(job)} for the job` : `${money(cap)} a month`,
      sub: `${job ? "stops when spent" : `pauses at ${money(wall)}`} · asks over $${asksAt.toLocaleString()}` },
    ...(p ? [dial("Say-so", "Autonomy", "autonomy"), dial("Pace", "Pace & brakes", "tempo"),
             dial("Quality bar", "The bar", "bar")] : []),
  ];
  return (
    <div className="pset-dials">
      {dials.map((d) => (
        <button key={d.name} type="button" className="pset-dial" onClick={() => onOpen(d.theme)}>
          <span className="k">{d.name}</span>
          <span className="v">{d.value}</span>
          <span className="d">{d.sub}</span>
        </button>
      ))}
    </div>
  );
}

function firstSentence(text: string): string {
  const cut = text.indexOf(". ");
  return cut > 0 ? text.slice(0, cut) : text;
}

// ── the section ──────────────────────────────────────────────────────────────

export const HISTORY_TAB = "History";

export function RulesSection({ rules, dash, theme, setTheme, flashKey }: {
  rules: Rules;
  dash: Dashboard;
  theme: string;
  setTheme: (theme: string) => void;
  flashKey: string | null;
}) {
  const [query, setQuery] = useState("");
  const { schema } = rules;
  const themes = [...new Set([...schema].sort((a, b) => a.theme_rank - b.theme_rank).map((f) => f.theme))];
  const active = theme === HISTORY_TAB || themes.includes(theme) ? theme : themes[0] ?? "";
  const dirtyIn = (name: string) => schema.filter((f) => f.theme === name && f.key in rules.changes).length;
  const q = query.trim().toLowerCase();

  let body: ReactNode;
  if (schema.length === 0) {
    body = <p className="help">Loading the rulebook…</p>;
  } else if (q) {
    const hits = schema.filter((f) => `${f.label} ${f.help} ${f.key}`.toLowerCase().includes(q));
    body = hits.length
      ? <div className="set-card">{hits.map((f) => (
          <RuleRow key={f.key} field={f} rules={rules} dormant={dormantReason(f.key, dash)} tag={f.theme} />))}</div>
      : <div className="set-card pad help">No rule matches “{query.trim()}”.</div>;
  } else if (active === HISTORY_TAB) {
    body = <History rules={rules} />;
  } else {
    const live = schema.filter((f) => f.theme === active && !dormantReason(f.key, dash));
    const asleep = schema.filter((f) => f.theme === active && dormantReason(f.key, dash));
    const groups = [...new Set(live.map((f) => f.group))];
    const preset = PRESET_TABS[active];
    body = <>
      {preset && rules.presets && (
        <Presets title={preset.title} cards={rules.presets[preset.family]} rules={rules} theme={active} />
      )}
      <div className="set-card">
        {groups.map((g) => (
          <Fragment key={g}>
            {groups.length > 1 && <div className="prule-grp">{g}</div>}
            {live.filter((f) => f.group === g).map((f) => (
              <RuleRow key={f.key} field={f} rules={rules} dormant={null} flash={flashKey === f.key} />
            ))}
          </Fragment>
        ))}
      </div>
      {asleep.length > 0 && (
        <details className="pset-asleep">
          <summary>Not enforced on this project: {asleep.length} rule{asleep.length === 1 ? "" : "s"}</summary>
          <div className="set-card">
            {asleep.map((f) => (
              <RuleRow key={f.key} field={f} rules={rules} dormant={dormantReason(f.key, dash)} />
            ))}
          </div>
        </details>
      )}
    </>;
  }

  return (
    <>
      <SectionHead title="Rules in force"
        lede="Every limit the platform enforces on this project. Agents can't move them; you can. A change is in force from the next agent call, recorded with your reason, and disclosed to the CEO."
        actions={
          <span className="pset-find">
            <Icon name="search" size={14} />
            <input type="search" value={query} placeholder="Find a rule" aria-label="Find a rule"
              onChange={(e) => setQuery(e.target.value)} />
          </span>
        } />
      {schema.length > 0 && <Dials rules={rules} usd={dash.billing_mode === "api_key"}
        onOpen={(t) => { setQuery(""); setTheme(t); }} />}
      <div className="tabs pset-tabs" role="tablist" aria-label="Rule groups">
        {[...themes, HISTORY_TAB].map((name) => {
          const n = name === HISTORY_TAB ? 0 : dirtyIn(name);
          return (
            <button key={name} type="button" role="tab" aria-selected={!q && name === active}
              className={`tab${!q && name === active ? " active" : ""}`}
              onClick={() => { setQuery(""); setTheme(name); }}>
              {name}
              {n > 0 && <span className="badge accent" aria-label={`${n} unsaved`}>{n}</span>}
            </button>
          );
        })}
      </div>
      {body}
    </>
  );
}

/** Every change ever made, newest first, in the rules' own words. */
function History({ rules }: { rules: Rules }) {
  const rows = rules.amendments;
  const [busy, setBusy] = useState(false);
  if (!rows) return <p className="help">Loading…</p>;
  if (rows.length === 0)
    return <div className="set-card pad help">Nothing changed yet. This project runs on the rules it was born with.</div>;
  return (
    <div className="set-card">
      <ul className="pset-history">
        {rows.map((a) => (
          <li key={a.id}>
            <div className="when">
              {fmtWhen(a.effective_at)}
              {a.status !== "applied" && <span className={`badge${a.status === "pending" ? " warn" : ""}`}>{a.status}</span>}
              {a.disclosed && <span className="help">told the CEO</span>}
            </div>
            <div>
              <div className="chg">
                {Object.entries(a.changes).map(([key, value]) => {
                  const f = rules.field(key);
                  return <span key={key}><b>{f?.label ?? key}</b> <span className="mono">→ {sayRule(f, value)}</span></span>;
                })}
              </div>
              <div className="why">“{a.reason}”</div>
              {/* Written while the old cooling-off existed; it can still wait. */}
              {a.status === "pending" && (
                <button type="button" className="small danger" disabled={busy} onClick={async () => {
                  setBusy(true);
                  try {
                    await post(`/admin/amendments/${a.id}/cancel`);
                    rules.reloadAmendments();
                  } finally {
                    setBusy(false);
                  }
                }}>Cancel this change</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── saving ───────────────────────────────────────────────────────────────────

/** The bar that rises when a rule is changed, on Rules in force or on Money:
    each change, the reason it is recorded with, and Review — which says what
    is about to be different before it is. */
export function RulesPending({ projectId, rules, onSaved, onError }: {
  projectId: string;
  rules: Rules;
  onSaved: (note: string) => void;
  onError: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const { changes, jsonKeys, jsonValues, badJson, reason } = rules;
  const chips = [
    ...Object.entries(changes).map(([k, v]) =>
      `${rules.field(k)?.label ?? k}: ${sayRule(rules.field(k), rules.cons[k])} → ${sayRule(rules.field(k), v)}`),
    ...jsonKeys.filter((k) => !(k in changes)).map((k) =>
      `${rules.field(k)?.label ?? k}: → ${sayRule(rules.field(k), jsonValues[k])} (JSON)`),
  ];
  const blocked = badJson ? "The JSON isn't valid"
    : reason.trim().length < 10 ? "Say why, in a sentence" : null;

  async function save() {
    setBusy(true);
    try {
      const merged = { ...jsonValues, ...changes };
      const result = await post<{ note: string }>(`/admin/projects/${projectId}/amendments`,
        { reason: reason.trim(), changes: merged });
      setConfirming(false);
      rules.setJson("{}");
      rules.setReason("");
      rules.reloadAmendments();
      onSaved(result.note);
    } catch (e) {
      setConfirming(false);
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PendingBar count={rules.count} what={rules.count === 1 ? "to a rule" : "to the rules"}
        note="Recorded with your reason and shown to the CEO." chips={chips}
        saveLabel={rules.count === 1 ? "Review change" : `Review ${rules.count} changes`}
        blocked={blocked} busy={busy} onDiscard={rules.discard} onSave={() => setConfirming(true)}>
        <input value={reason} aria-label="Why this change?"
          placeholder="Why? e.g. the pilot clinics need answers the same afternoon"
          onChange={(e) => rules.setReason(e.target.value)} />
      </PendingBar>
      {confirming && (
        <ConfirmModal title="This changes the rules your project runs under" confirmLabel="Change the rules"
          busy={busy} onConfirm={save} onCancel={() => setConfirming(false)}>
          <table>
            <thead><tr><th>Rule</th><th>Now</th><th>Becomes</th></tr></thead>
            <tbody>
              {Object.entries(changes).map(([k, v]) => (
                <tr key={k}>
                  <td>{rules.field(k)?.label ?? k}</td>
                  <td className="mono">{sayRule(rules.field(k), rules.cons[k])}</td>
                  <td className="mono"><b>{sayRule(rules.field(k), v)}</b></td>
                </tr>
              ))}
              {jsonKeys.filter((k) => !(k in changes)).map((k) => (
                <tr key={`json:${k}`}>
                  <td>{rules.field(k)?.label ?? k}<div className="help">from the JSON editor</div></td>
                  <td className="mono">{sayRule(rules.field(k), rules.cons[k])}</td>
                  <td className="mono"><b>{sayRule(rules.field(k), jsonValues[k])}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul style={{ margin: "10px 0", paddingLeft: 18 }}>
            <li><b>In force from the next agent call</b>, including runs already in flight.</li>
            <li><b>No undo window.</b> To go back, change it again; this version stays in the history.</li>
            <li><b>The CEO is told</b>, with your reason, and re-plans against the new limits.</li>
            <li>Loosening a limit can cost real money: ceilings, brakes and budgets are what stop a
              project spending through the night.</li>
          </ul>
          <p className="help" style={{ marginBottom: 0 }}>Recorded as: “{reason.trim()}”</p>
        </ConfirmModal>
      )}
    </>
  );
}
