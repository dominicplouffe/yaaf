import { CredentialPicker, resolveCredential } from "../llmCredentials";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { CrewSummary, get, McpServerEntry, post, ProjectBundle,
  RehearsalReport, RoleSummary, SlackProvision } from "../api";
import Drill from "../drill";
import {
  BLANK_DATA_SOURCE,
  DataSourceDraft,
  DataSourceFields,
  DataSourceIntro,
  dataSourceBody,
  envVarName,
  isFullUrl,
  RoleOption,
  savedName,
  sourceReady,
} from "../datasource";
import { CeoPicker, HirePanel, needsOf, roughMonthly } from "../roles";
import { SecretInput } from "../ui";
import type { FounderStatus } from "./StartProject";

interface TeamDraft {
  role_slug: string;
  name: string;
  employees: string; // comma-separated names
  lead: string;
  // Daily wake times per employee, carried in from a crew. "An overnight crew"
  // is nothing but its schedule, so dropping this on the way to submit would
  // hand the chairman a day-shift team wearing the wrong name.
  schedules?: Record<string, string>;
}

interface CredentialDraft {
  name: string;
  value: string;
  grants: string[]; // role slugs
}

function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

const MODES = [
  { value: "explore", label: "Explore",
    blurb: "Chase a goal with no fixed end. Bets, proof, and your call on when it has earned its keep.",
    example: "\u201CFind an idea and get one sale in 60 days.\u201D" },
  { value: "deliver", label: "Deliver",
    blurb: "Ship one thing, then stop. You accept what done means; the platform proves it.",
    example: "\u201CA security audit of our AWS estate.\u201D" },
  { value: "operate", label: "Operate",
    blurb: "Run the same play on a rhythm, and get better at it every cycle.",
    example: "\u201CA weekly blog post, tracked and improved.\u201D" },
] as const;

// The authority dial (docs/AUTONOMY.md): who holds the verbs. A preset is
// nothing but a bundle of five constitution switches — each editable
// individually later in Settings → Rules in force, changed by amendment.
// Every preset names all five explicitly so it overrides whatever a loaded
// setup bundle carried — an empty preset would silently inherit instead.
//
// Autonomous is first and selected by default, and the server agrees: a spec
// that says nothing about these five is created autonomous (see
// `constitution.DEFAULT_AUTONOMY`). A project a chairman has not said anything
// about is one he wants to run, not one he wants to operate by hand.
const AUTONOMY_PRESETS = [
  { value: "autonomous", label: "Autonomous",
    blurb: "The CEO accepts its own plan, signs the platform's projected date, and " +
      "judges unverifiable closes itself. Every decision is audited and sent to " +
      "you; all of it is reversible; your buttons never close.",
    example: "The default. The CEO runs the project; you chair it. Budgets and " +
      "brakes still bind.",
    constitution: { plan_authority: "ceo", scope_authority: "ceo",
      commitment_authority: "ceo", verdict_policy: "ceo_judgment",
      capability_authority: "ceo" } },
  { value: "supervised", label: "Supervised",
    blurb: "You approve scope, sign the date, and judge unverifiable closes. " +
      "The CEO proposes — and executes any order you give it in the boardroom.",
    example: "Right for a first project, or real money on the line. It waits for " +
      "you, so nothing moves while you are away.",
    constitution: { plan_authority: "chairman", scope_authority: "chairman",
      commitment_authority: "chairman", verdict_policy: "chairman",
      capability_authority: "chairman" } },
] as const;

// How the project runs day to day: pace, money, proof — and, since the bar
// landed, how finished the work has to be. A preset is nothing but a bundle of
// ordinary constitution numbers — picking one fills the fields below, every
// field stays editable here and later in Settings → Rules in force.
// Deliberately orthogonal to the authority presets above: these never touch
// who holds the verbs.
//
// Two things here are not arbitrary. "Full send" used to say "as fast as
// possible", which made the only card a chairman would pick for excellent work
// the one optimising for the clock — and speed is not this platform's scarce
// resource, since the median project finishes its whole accepted scope inside
// a day. Cost being secondary now buys DEPTH. And the cheap card is labelled
// cheap: it used to say "Slow & steady", which reads as take-your-time-and-do-
// it-properly, over the settings that produce the least finished work anybody
// can order here.
//
// Every card asks for an independent read, because the review is one extra run
// per deliverable and the expensive part is the model, not the reader. What
// separates the cards is money, pace, proof and which model the producing
// seats are hired on.
const RUN_PRESETS = [
  { value: "full_send", label: "Full send",
    blurb: "Nothing spared. The seats that write and check the work run on the " +
      "strongest model at the deepest reasoning, with the per-run ceiling and " +
      "timeout raised so a long piece is not cut off mid-thought. Big budget, " +
      "big queue, hourly reports.",
    knobs: { queue: 16, drought: 72, cap: 5000, threshold: 1000, verify: false, report: 1,
             stakes: "high", review: "independent_read", runUsd: 8, runTimeout: 3600 } },
  { value: "balanced", label: "Balanced",
    blurb: "Steady pace, a real budget, a report every hour, and the work read " +
      "by somebody who did not write it. The default for a reason.",
    knobs: { queue: 8, drought: 24, cap: 1000, threshold: 250, verify: false, report: 1,
             stakes: "normal", review: "independent_read", runUsd: 2, runTimeout: 1800 } },
  { value: "frugal", label: "Cheap and patient",
    blurb: "Small budget, small queue, a report every few hours — still read " +
      "before it reaches you. Right for a long-running side project. The read " +
      "costs a run per deliverable, so expect roughly a quarter less throughput " +
      "for it.",
    knobs: { queue: 4, drought: 48, cap: 200, threshold: 50, verify: false, report: 4,
             stakes: "low", review: "independent_read", runUsd: 2, runTimeout: 1800 } },
  { value: "show_me_proof", label: "Show me proof",
    blurb: "External validation matters: evidence counts only once the platform " +
      "verifies it, and a proof drought blocks busywork.",
    knobs: { queue: 8, drought: 24, cap: 1000, threshold: 250, verify: true, report: 1,
             stakes: "normal", review: "independent_read", runUsd: 2, runTimeout: 1800 } },
] as const;

// Remembered across projects — there is one chairman on this install, and
// retyping your own email every time was pure friction.
/** The four decisions, and the one line each is answering. Shown in the rail
    so the chairman knows how much is left before they start typing. */
const STEPS = [
  { n: 1, name: "The job", detail: "What this project is for" },
  { n: 2, name: "The org", detail: "Who does the work" },
  { n: 3, name: "The money & the rules", detail: "What it may spend, how it behaves" },
  { n: 4, name: "Review & create", detail: "What you're signing" },
];

/** Does the brief say the four things a CEO needs to plan from it?

    Straight out of CREATING_A_PROJECT.md, which spends twenty lines on how to
    write a goal — advice that until now lived only in the docs while the form
    offered a 90px box and no help at all. Heuristics, deliberately loose:
    this nudges, it never blocks, and a goal with none of them ticked still
    creates a project. */
function briefChecks(goal: string): { label: string; ok: boolean }[] {
  const g = goal.toLowerCase();
  return [
    {
      label: "Names an outcome, not an activity",
      ok: /\b(produce|deliver|ship|build|audit|answer|find|publish|reach|win)\w*\b/.test(g),
    },
    {
      label: "Names a deliverable and who it's for",
      ok: /\b(deck|report|brief|memo|post|article|dashboard|game|curriculum|audit|analysis|newsletter|proposal|site|app)\w*\b/.test(g),
    },
    {
      label: "Gives a deadline",
      ok: /\b(by|before|within|each|every)\b/.test(g)
        && /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{4}-\d{2}-\d{2}|\d{1,2}(st|nd|rd|th)|day|week|month|quarter)\w*\b/.test(g),
    },
    {
      label: "Says where the inputs come from",
      ok: /\b(data source|from the|provided|connected|attached|account|repo|repository|export|spreadsheet|inbox|api)\w*\b/.test(g),
    },
  ];
}

const CHAIRMAN_KEY = "yaaf.chairman";
const ONE_PAGE_KEY = "yaaf.newproject.onepage";

export default function NewProject() {
  const navigate = useNavigate();
  // "Load a setup file" is offered on the start screen as a third way in, but
  // it is a card on this form rather than a screen of its own — a file is a
  // prefill, not a different decision. The deep link brings the reader to it
  // instead of asking them to spot it.
  const [search] = useSearchParams();
  const fileCard = useRef<HTMLDivElement | null>(null);
  // Bumped by every bundle load and every manual pick, so a credential lookup
  // that started for an earlier bundle cannot land on top of a later choice.
  const billingPick = useRef(0);
  // A draft handed over by the Founder. It arrives as the same ProjectBundle a
  // setup file carries, so it lands through the same applyBundle path; the
  // thread id rides along so the founding conversation can be handed to the
  // CEO once the project exists.
  const handover = (useLocation().state ?? {}) as {
    foundingDraft?: ProjectBundle;
    foundingThreadId?: string;
  };
  const [foundingThreadId] = useState<string | null>(handover.foundingThreadId ?? null);
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  // Distinct from `roles.length`: an install whose library is genuinely empty
  // must still be told which roles a setup file wanted, and a file dropped
  // before the fetch lands must not be judged against a list that is only
  // empty because it has not arrived.
  const [rolesLoaded, setRolesLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  // Chosen for you from the name until you touch it — then it is yours.
  const [slugEdited, setSlugEdited] = useState(false);
  const [existingSlugs, setExistingSlugs] = useState<string[]>([]);
  const [goal, setGoal] = useState("");
  const [chairmanEmail, setChairmanEmail] = useState("");
  const [chairmanName, setChairmanName] = useState("");
  const [billingMode, setBillingMode] = useState("api_key");
  const [billingCredentialId, setBillingCredentialId] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [mode, setMode] = useState("explore");
  // Set when a loaded setup file could not answer the shape question itself —
  // the one prefill that must never be guessed silently, because a project's
  // mode is frozen at creation and Explore hides the Plan tab by design.
  const [modeNotice, setModeNotice] = useState<string | null>(null);
  const [exploreDeadline, setExploreDeadline] = useState("");
  // Autonomous unless the chairman says otherwise — the same default the
  // server applies to a spec that never mentions the dial.
  const [autonomy, setAutonomy] = useState("autonomous");
  // Set when a setup file's Autonomy group is not either preset exactly — a
  // constitution hand-edited in Settings, which no button here can represent.
  const [autonomyNotice, setAutonomyNotice] = useState<string | null>(null);
  const [runtime, setRuntime] = useState("agent_sdk");
  const [controlledDomains, setControlledDomains] = useState("");
  const [teams, setTeams] = useState<TeamDraft[]>([
    { role_slug: "", name: "", employees: "", lead: "" },
  ]);
  const [credentials, setCredentials] = useState<CredentialDraft[]>([]);
  const [dataSources, setDataSources] = useState<DataSourceDraft[]>([]);

  // Constitution knobs surfaced in the wizard — a run preset fills them,
  // every one stays editable, and all of them can change later in Settings.
  const [runPreset, setRunPreset] = useState("balanced");
  // The craft half of a preset. No form fields of their own — the same choice
  // `max_usd_per_run` has always made here — so they ride the card and are
  // edited in Settings → Rules in force afterwards.
  const [craft, setCraft] = useState(() => {
    const k = RUN_PRESETS.find((p) => p.value === "balanced")!.knobs;
    return { stakes: k.stakes, review: k.review, runUsd: k.runUsd,
             runTimeout: k.runTimeout } as {
      stakes: string; review: string; runUsd: number; runTimeout: number };
  });
  const [queueCeiling, setQueueCeiling] = useState(8);
  const [droughtHours, setDroughtHours] = useState(24);
  const [usdCap, setUsdCap] = useState(1000);
  // The same cap in the subscription unit. Which one binds is decided by the
  // credential, not the form: a subscription meters tokens and the USD figure
  // is never consulted, so the form shows and posts whichever one applies.
  const [tokenCap, setTokenCap] = useState(200_000_000);
  const tokensMode = billingMode === "subscription";
  const [approvalThreshold, setApprovalThreshold] = useState(250);
  const [requireVerification, setRequireVerification] = useState(false);
  const [reportHours, setReportHours] = useState(1);
  const [reportWindow, setReportWindow] = useState("");
  const [timezone, setTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [startNow, setStartNow] = useState(true);
  // Which CEO runs it. Was never asked: the form took whichever CEO role the
  // database returned first, so an install carrying both `ceo` and `cio` could
  // hand a markets desk the generic CEO — the one agent the chairman ever
  // speaks to. Empty until the library lands, then defaulted below.
  const [ceoSlug, setCeoSlug] = useState("");
  // Which team row is choosing a role, or null. One panel, reused per row.
  const [hireOpen, setHireOpen] = useState<number | null>(null);
  const [crews, setCrews] = useState<CrewSummary[]>([]);

  // Four steps, because there are four decisions here that need the
  // chairman's judgement: what the project is for, who does the work, what it
  // may spend, and whether to sign it. Everything else is derived, filled by a
  // preset, or something this form already says you can do later in Settings.
  //
  // `onePage` renders all four stacked — the same fields, the same state, the
  // same submit. It exists because a wizard's one real cost is that you cannot
  // see everything at once, and because someone reviewing a loaded setup file
  // wants the whole thing in front of them.
  const [step, setStep] = useState(1);
  const [onePage, setOnePage] = useState(
    () => localStorage.getItem(ONE_PAGE_KEY) === "yes");
  function setView(one: boolean) {
    setOnePage(one);
    localStorage.setItem(ONE_PAGE_KEY, one ? "yes" : "no");
  }
  const on = (n: number) => onePage || step === n;

  // A setup file downloaded from a project's Settings. Loading one prefills
  // the whole form; the file's full constitution/config and any role
  // definitions the library lacks ride along at submit time.
  const [bundle, setBundle] = useState<ProjectBundle | null>(null);
  // Only for the cost line under the run presets: what a run has cost here.
  const [status, setStatus] = useState<FounderStatus | null>(null);
  const [solo, setSolo] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [webhooks, setWebhooks] = useState<{
    projectId: string;
    github: { url: string; secret: string };
    generic: { url: string; secret: string };
  } | null>(null);
  // What Slack rooms the platform made on the way in, when an app-wide
  // Slack connection is configured. The whole point of app-wide Slack settings
  // is that this screen can answer "and where do I talk to it?" without sending
  // anybody to Slack to click New Channel five times.
  const [slack, setSlack] = useState<SlackProvision | null>(null);

  useEffect(() => {
    // A library with no crews is normal before `yaaf seed-crews` has run, and
    // must never take the role list down with it.
    get<CrewSummary[]>("/admin/crews").then(setCrews).catch(() => {});
    // Best-effort: the cost line says so itself when the number is missing.
    get<FounderStatus>("/admin/founder/status").then(setStatus).catch(() => {});
    get<RoleSummary[]>("/admin/roles")
      .then((rows) => {
        setRoles(rows);
        setRolesLoaded(true);
        // Default, not a silent decision: `ceo` if it exists, else the first
        // CEO role the library has. The chairman sees which one is selected
        // and can change it.
        setCeoSlug((current) => {
          if (current) return current;
          const ceos = rows.filter((r) => r.is_ceo_role);
          return ceos.find((r) => r.slug === "ceo")?.slug ?? ceos[0]?.slug ?? "";
        });
      })
      .catch((e: Error) => setError(e.message));
    // Existing slugs, so the auto-picked one can dedupe without a failed submit.
    get<{ slug: string }[]>("/observe/projects")
      .then((rows) => setExistingSlugs(rows.map((r) => r.slug)))
      .catch(() => {});
    try {
      const saved = JSON.parse(localStorage.getItem(CHAIRMAN_KEY) ?? "null") as
        { email?: string; name?: string } | null;
      if (saved?.email) setChairmanEmail(saved.email);
      if (saved?.name) setChairmanName(saved.name);
    } catch { /* a corrupt saved value is just a blank form */ }
  }, []);

  function dedupedSlug(base: string): string {
    if (!base || !existingSlugs.includes(base)) return base;
    for (let n = 2; ; n++) {
      const suffix = `-${n}`;
      // Trim the base, not the suffix: slicing `${base}-${n}` to 64 when base
      // is already 64 chars drops the suffix entirely, so every candidate
      // equals the taken base and the loop never ends.
      const candidate = base.slice(0, 64 - suffix.length) + suffix;
      if (!existingSlugs.includes(candidate)) return candidate;
    }
  }

  function applyRunPreset(value: string) {
    setRunPreset(value);
    const knobs = RUN_PRESETS.find((p) => p.value === value)?.knobs;
    if (!knobs) return;
    setQueueCeiling(knobs.queue);
    setDroughtHours(knobs.drought);
    setUsdCap(knobs.cap);
    setApprovalThreshold(knobs.threshold);
    setRequireVerification(knobs.verify);
    setReportHours(knobs.report);
    setCraft({ stakes: knobs.stakes, review: knobs.review,
               runUsd: knobs.runUsd, runTimeout: knobs.runTimeout });
  }

  const ceoRole = roles.find((r) => r.is_ceo_role);

  // Every run preset asks for the work to be read by somebody who did not
  // write it, and the server refuses a roster that asks for that and hires
  // nobody who can. Without this the chairman fills the whole form and meets
  // a 422 at the end — the refusal is right and discovering it there is not.
  // Solo mode is exempt: the CEO is the team, so there is no second reader to
  // hire and the setting has nobody to bind.
  const reviewRoles = roles.filter((r) => r.category === "challenge");
  const hasReviewSeat = teams.some(
    (t) => reviewRoles.some((r) => r.slug === t.role_slug));
  // Deliberately NOT gated on the roster being non-empty. It was, and that hid
  // the warning for exactly the shape the server refuses hardest — a blank
  // roster asking for a read it has not staffed — so the one case where the
  // chairman most needs telling was the one case he was not told.
  const needsReviewSeat =
    craft.review === "independent_read" && !solo && !hasReviewSeat;

  function addReviewTeam() {
    // `editor` is the seat written for prose; anything else in the discipline
    // reads work too, so the first one installed will do.
    const pick = reviewRoles.find((r) => r.slug === "editor") ?? reviewRoles[0];
    if (!pick) return;
    setTeams((prev) => [...prev,
      { role_slug: pick.slug, name: "Review", employees: "Juno", lead: "" }]);
  }

  function setTeam(i: number, patch: Partial<TeamDraft>) {
    setTeams((prev) => prev.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  }

  // Roles actually used by the drafted teams — the only sensible grant targets.
  const usedRoleSlugs = [...new Set(teams.map((t) => t.role_slug).filter(Boolean))];
  const hiredRoles = usedRoleSlugs
    .map((slug) => roles.find((r) => r.slug === slug))
    .filter(Boolean) as RoleSummary[];

  /** The keys this staffing actually needs, gathered from the roles hired.

      Turned the right way round: the form used to offer a blank credential and
      ask which roles should receive it, which meant knowing what a Market Data
      Engineer wants before you had one. Every role has always declared its
      secret_scopes; this reads them and names the key, so a setup blocker
      appears while you are choosing rather than at the 4xx after submit. */
  const requiredScopes = (() => {
    const byScope = new Map<string, { scope: string; words: string; roles: string[] }>();
    for (const role of hiredRoles) {
      for (const need of needsOf(role.secret_scopes ?? [])) {
        const row = byScope.get(need.scope)
          ?? { scope: need.scope, words: need.words, roles: [] };
        row.roles.push(role.name);
        byScope.set(need.scope, row);
      }
    }
    return [...byScope.values()];
  })();

  /** A step with something wrong on it gets a dot in the rail, rather than the
      chairman finding out at submit after twenty-eight fields. */
  function problemsOn(n: number): string | null {
    if (n === 1) {
      if (!name.trim()) return "This project needs a name";
      if (goal.trim().length < 10) return "The CEO plans from the goal — it cannot be empty";
      return null;
    }
    if (n === 2) {
      if (missingRoleSlugs.length) {
        return `This install's library has no ${missingRoleSlugs.join(", ")}`;
      }
      if (!ceoSlug && !bundle?.project.ceo_role_slug) return "Nobody is running this project";
      const staffed = teams.filter((t) => t.role_slug && t.name.trim());
      if (!staffed.length) return "Nobody is doing the work";
      if (!solo && staffed.some((t) => !t.employees.trim())) {
        return "A team with no employees does no work";
      }
      return null;
    }
    if (n === 3) {
      if (!billingCredentialId.trim() && runtime !== "mock") {
        return "Without a billing key the project is created but cannot start";
      }
      return null;
    }
    return null;
  }

  function setCredential(i: number, patch: Partial<CredentialDraft>) {
    setCredentials((prev) => prev.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  }

  function setDataSource(i: number, next: DataSourceDraft) {
    setDataSources((prev) => prev.map((s, j) => (j === i ? next : s)));
  }

  // Start needs a model to bill: a real key here, or the mock runtime. A
  // project can always be created without one and connected in Settings —
  // it just cannot START, because its first run would die hunting for a key.
  const canStartNow = runtime === "mock" || !!billingCredentialId.trim();

  /** Fill the whole form from a setup bundle.
   *
   *  Two things arrive here and they must land identically: a file the
   *  chairman exported, and a draft the Founder wrote. A second copy of this
   *  for the Founder would be a second place for "what the file said" and
   *  "what the form shows" to drift apart, which is the one bug this page
   *  cannot afford. */
  function applyBundle(parsed: ProjectBundle) {
    try {
      const p = parsed.project;
      setBundle(parsed);
      setName(p.name);
      setSlug(dedupedSlug(p.slug));
      setGoal(p.goal);
      setBillingMode(p.billing_mode);
      if (p.mode) {
        setMode(p.mode);
        setModeNotice(null);
      } else {
        // A file from before project shapes existed. Guessing Explore here
        // once silently overwrote a chairman's Deliver choice — keep whatever
        // is selected and say the file had no opinion.
        setModeNotice(
          "This setup file predates project shapes, so it does not say what kind of " +
          "project it was. Nothing was changed — confirm the choice above is what you want.",
        );
      }
      setRuntime(String(p.integrations["runtime"] ?? "agent_sdk"));
      setControlledDomains((p.controlled_domains ?? []).join(", "));
      setSolo(Boolean(p.solo));
      // Who ran the source project. A prefill like the rest of the form —
      // change it if the twin answers to someone else.
      if (p.chairman_email) setChairmanEmail(p.chairman_email);
      // An Explore window that is still open carries; a spent one would be
      // refused at create (a project the janitor closes at birth), so it is
      // left blank rather than prefilled into a guaranteed error.
      if (p.deadline && new Date(p.deadline) > new Date()) {
        setExploreDeadline(p.deadline.slice(0, 10));
      }
      // A draft or a backup names who pays — by id from the Founder, by name
      // from an export. Either is resolved against what exists now; a name
      // that matches nothing leaves the picker empty, and the step says so.
      setBillingCredentialId("");
      const pick = ++billingPick.current;
      resolveCredential(p.billing_secret_id, p.billing_secret_name).then((found) => {
        if (found && pick === billingPick.current) { setBillingCredentialId(found.id); setBillingMode(found.mode); }
      });
      // The file's whole job here is review-and-supply-keys, so land on the
      // step that shows the project it describes rather than the first field
      // of eleven sections it already filled.
      if (p.ceo_role_slug) setCeoSlug(p.ceo_role_slug);
      setStep(4);
      setTeams(
        p.teams.length
          ? p.teams.map((t) => ({
              role_slug: t.role_slug,
              name: t.name,
              employees: t.employees.join(", "),
              lead: t.lead ?? "",
            }))
          : [{ role_slug: "", name: "", employees: "", lead: "" }],
      );
      setCredentials(
        parsed.secrets
          .filter((s) => s.kind === "service")
          .map((s) => ({ name: s.name, value: s.value ?? "", grants: s.role_scopes })),
      );
      // Who holds the verbs travels in the file too, and the submit below
      // stamps this preset over whatever the file said — so a file exported
      // from an autonomous project came back supervised, and one exported
      // from a supervised project could come back autonomous, neither with a
      // word about it. Same defect as a role slug the picker could not show:
      // the form dropped the file's answer silently. Read it instead.
      const authority = p.constitution as Record<string, string>;
      const stated = AUTONOMY_PRESETS.find((preset) =>
        Object.entries(preset.constitution).every(([k, v]) => authority[k] === v));
      const named = Object.keys(AUTONOMY_PRESETS[0].constitution)
        .filter((k) => authority[k] !== undefined);
      if (stated) {
        setAutonomy(stated.value);
        setAutonomyNotice(null);
      } else if (named.length) {
        // A mixed group is a real choice somebody made rule by rule in
        // Settings. Neither button is it, so say which rules the file carries
        // and leave the selection alone rather than rounding it to a preset.
        setAutonomyNotice(
          `This setup file sets ${named.map((k) => `${k}: ${authority[k]}`).join(", ")}, ` +
          "which is not either preset. Picking one below replaces all five rules — " +
          "or create it as-is and adjust in Settings → Rules in force.",
        );
      } else {
        setAutonomyNotice(null);
      }
      const cons = p.constitution as Record<string, number>;
      if (cons.queue_ceiling !== undefined) setQueueCeiling(cons.queue_ceiling);
      if (cons.drought_hours !== undefined) setDroughtHours(cons.drought_hours);
      if (cons.monthly_usd_cap !== undefined) setUsdCap(cons.monthly_usd_cap);
      if (cons.monthly_token_cap !== undefined) setTokenCap(cons.monthly_token_cap);
      if (cons.approval_threshold_usd !== undefined) {
        setApprovalThreshold(cons.approval_threshold_usd);
      }
      // The craft half, when a file or a Founder draft has an opinion about
      // it. Left alone otherwise, so a spec written before these rules existed
      // keeps whatever card is selected rather than being silently downgraded.
      const craftCons = p.constitution as Record<string, string | number>;
      setCraft((prev) => ({
        stakes: (craftCons.stakes as string) ?? prev.stakes,
        review: (craftCons.deliverable_review as string) ?? prev.review,
        runUsd: (craftCons.max_usd_per_run as number) ?? prev.runUsd,
        runTimeout: (craftCons.run_timeout_s as number) ?? prev.runTimeout,
      }));
    } catch (err) {
      setBundle(null);
      setError((err as Error).message);
    }
  }

  // Once, on arrival. A draft is a proposal to review, so it fills the form
  // and stops there — the chairman still presses Create.
  useEffect(() => {
    if (handover.foundingDraft) applyBundle(handover.foundingDraft);
    if (search.get("start") === "file") {
      fileCard.current?.scrollIntoView({ block: "center" });
      fileCard.current?.querySelector("input")?.focus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadBundle(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    try {
      const parsed = JSON.parse(await file.text()) as ProjectBundle;
      if (parsed.yaaf_bundle !== 1 || !parsed.project) {
        throw new Error(
          "that file is not a project setup file — download one from a project's Settings",
        );
      }
      applyBundle(parsed);
    } catch (err) {
      setBundle(null);
      setError((err as Error).message);
    }
  }

  // Bundle roles the library doesn't have yet — offered in the pickers now,
  // created for real (POST /admin/roles) at submit time.
  const newBundleRoles = (bundle?.roles ?? []).filter(
    (br) => !roles.some((r) => r.slug === br.slug),
  );

  // Slugs a setup file names that this install cannot supply: not in the
  // library, and no definition carried in the file either (the examples hire
  // from the library rather than shipping their roles). A <select> whose value
  // matches no <option> falls back to the placeholder, so before this the form
  // simply showed "— pick a role —" and the chairman was left to guess which
  // role the file had asked for. Derived rather than computed inside
  // loadBundle: the library arrives on its own fetch, and a file dropped
  // before it lands must not be judged against an empty list.
  const known = (slug: string) =>
    !slug || roles.some((r) => r.slug === slug) || newBundleRoles.some((r) => r.slug === slug);
  const bundleCeoSlug = bundle?.project.ceo_role_slug ?? "";
  const missingRoleSlugs = rolesLoaded
    ? [...new Set([...teams.map((t) => t.role_slug), bundleCeoSlug].filter((s) => s && !known(s)))]
    : [];

  // Every role, not just the worker ones: the review step names the CEO
  // through here too, and a CEO role searched only among workers never
  // resolves — which showed the chairman "cio" where the name should be.
  function roleLabel(slug: string): string {
    return (
      roles.find((r) => r.slug === slug)?.name ??
      newBundleRoles.find((r) => r.slug === slug)?.name ??
      slug
    );
  }

  // The drafted teams as grant targets, in the shape Settings' tick boxes use:
  // one entry per distinct role, carrying the team(s) that run it. A team is
  // named the moment it has a role, so the role's own name stands in until then.
  const roleOptions: RoleOption[] = [];
  for (const team of teams) {
    if (!team.role_slug) continue;
    const label = team.name.trim() || roleLabel(team.role_slug);
    const existing = roleOptions.find((option) => option.slug === team.role_slug);
    if (existing) {
      if (!existing.teams.includes(label)) existing.teams.push(label);
    } else roleOptions.push({ slug: team.role_slug, teams: [label] });
  }

  // A setup file carries its data sources' wiring but not their addresses —
  // those are secrets, and secrets stay behind. So some rows in the credential
  // list below are not opaque keys at all: they are URLs the sandbox hands
  // straight to an MCP client. Knowing which lets this page check them here,
  // rather than let an agent discover it three runs later. Secret name → source.
  const dataSourceUrls = new Map<string, string>(
    ((bundle?.project.integrations["mcp_servers"] as McpServerEntry[] | undefined) ?? []).map(
      (source) => [source.url_secret, source.name] as [string, string],
    ),
  );

  function toggleGrant(i: number, slug: string) {
    setCredentials((prev) =>
      prev.map((c, j) =>
        j === i
          ? {
              ...c,
              grants: c.grants.includes(slug)
                ? c.grants.filter((g) => g !== slug)
                : [...c.grants, slug],
            }
          : c,
      ),
    );
  }

  // The fire drill. Nothing here creates anything: the spec is run as a
  // project in a throwaway process on the mock runtime, and what comes back is
  // a report. It is the one thing on this form that can answer "will this
  // work?" before the billing key is attached.
  const [drill, setDrill] = useState<RehearsalReport | null>(null);
  const [drilling, setDrilling] = useState(false);
  const [drillError, setDrillError] = useState<string | null>(null);

  async function rehearse() {
    setDrilling(true);
    setDrillError(null);
    setDrill(null);
    try {
      const chosenCeo = bundle?.project.ceo_role_slug ?? ceoSlug ?? ceoRole?.slug;
      if (!chosenCeo) throw new Error("Pick a CEO role first.");
      // Role definitions a fresh library lacks travel with the spec, so the
      // drill can resolve them without anything being written here.
      const spec = { project: projectBody(chosenCeo), roles: newBundleRoles };
      setDrill(await post<RehearsalReport>("/admin/rehearse", { spec, days: 30 }));
    } catch (e) {
      setDrillError((e as Error).message);
    } finally {
      setDrilling(false);
    }
  }

  /** The project exactly as it will be POSTed — extracted so the fire drill
      can run the SAME object the submit button would send. A second copy
      assembled for the rehearsal would be a rehearsal of a different
      project, which is worse than no rehearsal at all. */
  function projectBody(chosenCeo: string) {
    return {
          slug: slug.trim(),
          name: name.trim(),
          goal: goal.trim(),
          chairman_email: chairmanEmail.trim(),
          billing_mode: billingMode,
          billing_secret_id: billingCredentialId || undefined,
          mode,
          ...(mode === "explore" && exploreDeadline
            ? { deadline: `${exploreDeadline}T23:59:59Z` } : {}),
          controlled_domains: controlledDomains
            .split(",")
            .map((d) => d.trim())
            .filter(Boolean),
          constitution: {
            // A setup file carries the source's full rulebook; the knobs and
            // the authority preset below override whatever you edited here.
            ...(bundle?.project.constitution ?? {}),
            queue_ceiling: queueCeiling,
            drought_hours: droughtHours,
            monthly_usd_cap: usdCap,
            monthly_token_cap: tokenCap,
            approval_threshold_usd: approvalThreshold,
            require_evidence_verification: requireVerification,
            stakes: craft.stakes,
            deliverable_review: craft.review,
            // The brakes move with the tier or the card is a lie: a writer on
            // the strongest model at the deepest reasoning, under a $2 ceiling
            // and a 30-minute timeout, dies mid-piece.
            max_usd_per_run: craft.runUsd,
            run_timeout_s: craft.runTimeout,
            chairman_report_interval_hours: reportHours,
            chairman_report_window_local: reportWindow.trim() || null,
            timezone: timezone.trim() || "UTC",
            // Skipped while a file's hand-mixed Autonomy group stands unanswered:
            // rounding five deliberate rules to the nearest button is the silent
            // overwrite this notice exists to stop. Clicking a preset clears it.
            ...(autonomyNotice
              ? {}
              : AUTONOMY_PRESETS.find((p) => p.value === autonomy)?.constitution ?? {}),
          },
          integrations: { ...(bundle?.project.integrations ?? {}), runtime },
          ...(bundle?.project.github_repo ? { github_repo: bundle.project.github_repo } : {}),
          ceo_role_slug: chosenCeo,
          ceo_name: bundle?.project.ceo_name ?? "CEO",
          ...(bundle?.project.ceo_daily_at ? { ceo_daily_at: bundle.project.ceo_daily_at } : {}),
          ...(bundle?.project.ceo_model_override
            ? { ceo_model_override: bundle.project.ceo_model_override } : {}),
          ...(bundle?.project.ceo_effort_override
            ? { ceo_effort_override: bundle.project.ceo_effort_override } : {}),
          solo,
          teams: teams
            .filter((t) => t.role_slug && t.name && (solo || t.employees.trim()))
            .map((t) => {
              const employees = t.employees.split(",").map((s) => s.trim()).filter(Boolean);
              // Daily wakes and model overrides ride the setup file per person.
              // Filtered to the names still on the form: renaming an employee
              // here must not turn into a refusal about someone who left.
              const fromBundle = bundle?.project.teams.find((bt) => bt.name === t.name.trim());
              const keep = (settings?: Record<string, string>) =>
                Object.fromEntries(
                  Object.entries(settings ?? {}).filter(([n]) => employees.includes(n)));
              // A crew's wakes and a file's wakes reach here the same way; the
              // file wins when both are present, because loading one is the more
              // deliberate act.
              const schedules = keep({ ...t.schedules, ...fromBundle?.schedules });
              const modelOverrides = keep(fromBundle?.model_overrides);
              const effortOverrides = keep(fromBundle?.effort_overrides);
              return {
                role_slug: t.role_slug,
                name: t.name.trim(),
                employees,
                lead: t.lead.trim() || undefined,
                ...(Object.keys(schedules).length ? { schedules } : {}),
                ...(Object.keys(modelOverrides).length
                  ? { model_overrides: modelOverrides } : {}),
                ...(Object.keys(effortOverrides).length
                  ? { effort_overrides: effortOverrides } : {}),
              };
            }),
    };
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // A mistyped data source address costs nothing to catch here and a great
      // deal to catch later: the runner refuses to mount it, and says so only
      // inside the run summary of whichever agent went looking for the tools.
      const malformed = credentials.find(
        (c) => dataSourceUrls.has(c.name.trim()) && c.value.trim() && !isFullUrl(c.value),
      );
      if (malformed) {
        throw new Error(
          `"${malformed.name}" is the address of data source ` +
            `"${dataSourceUrls.get(malformed.name.trim())}" — it needs a full URL like ` +
            "https://host:3845/mcp, not a bare hostname or a token.",
        );
      }
      // A card left entirely blank is an unused card. Anything typed into one
      // has to be complete here, because a half-filled source would otherwise
      // be dropped without a word and be missed mid-run. Grants are pruned to
      // the roles the drafted teams still run: a tick survives in state after
      // its team's role is changed, and the source must not carry it.
      const savedSources: { name: string; body: ReturnType<typeof dataSourceBody> }[] = [];
      for (const source of dataSources) {
        const sourceName = savedName(source);
        if (!sourceName && !source.url.trim()) continue;
        if (sourceName.length < 2) {
          throw new Error(
            "A data source needs a name of at least two characters — it becomes the " +
              "env var its roles receive and the prefix of its tools.",
          );
        }
        if (savedSources.some((s) => s.name === sourceName)) {
          throw new Error(
            `Two data sources are both called "${sourceName}" — one name, one source: ` +
              "the second would overwrite the first.",
          );
        }
        if (!isFullUrl(source.url)) {
          throw new Error(
            `Data source "${sourceName}" needs a full URL like https://host:3845/mcp.`,
          );
        }
        const granted = source.roles.filter((slug) => usedRoleSlugs.includes(slug));
        if (!sourceReady(source) || granted.length === 0) {
          throw new Error(
            `Data source "${sourceName}" is granted to no roles — nobody would ` +
              "receive its tools. Tick at least one role, or remove the row.",
          );
        }
        savedSources.push({
          name: sourceName,
          body: { ...dataSourceBody(source), roles: granted },
        });
      }
      // A role nobody can resolve fails at the server as an opaque 4xx after
      // the chairman has filled the whole form in. Name it here instead.
      if (missingRoleSlugs.length) {
        throw new Error(
          `This install's role library has no ${missingRoleSlugs.join(", ")}. ` +
            "Load them from your checkout with `yaaf seed-roles roles/ --only-missing` " +
            "(or ./update_single_server.sh), then reload this page — or pick roles this " +
            "install already has.",
        );
      }
      // Role definitions travel with a setup file — seed any the library
      // lacks before the server resolves slugs. Re-check against a fresh
      // list so a retried submit never stamps duplicate versions.
      if (newBundleRoles.length) {
        const library = await get<RoleSummary[]>("/admin/roles");
        for (const role of newBundleRoles) {
          if (!library.some((r) => r.slug === role.slug)) await post("/admin/roles", role);
        }
        setRoles(await get<RoleSummary[]>("/admin/roles"));
      }
      // The file's choice wins when one was loaded — its teams were designed
      // around that CEO — otherwise the chairman's.
      const chosenCeo = bundle?.project.ceo_role_slug ?? ceoSlug ?? ceoRole?.slug;
      if (!chosenCeo) throw new Error("No CEO role in the library — create one on the Roles page first.");
      await post("/admin/users", {
        email: chairmanEmail.trim(),
        display_name: chairmanName.trim() || chairmanEmail.trim(),
      });
      try {
        localStorage.setItem(CHAIRMAN_KEY, JSON.stringify(
          { email: chairmanEmail.trim(), name: chairmanName.trim() }));
      } catch { /* remembering is a convenience, never a requirement */ }
      const body = projectBody(chosenCeo);
      const created = await post<{
        id: string;
        slack?: SlackProvision | null;
        webhooks?: { github: { url: string; secret: string };
                     generic: { url: string; secret: string } };
      }>("/admin/projects", body);
      if (created.webhooks) setWebhooks({ projectId: created.id, ...created.webhooks });
      if (created.slack) setSlack(created.slack);
      try {
        for (const cred of credentials) {
          if (!cred.name.trim() || !cred.value) continue;
          await post("/admin/secrets", {
            project_id: created.id,
            name: cred.name.trim(),
            value: cred.value,
            role_scopes: cred.grants.filter((g) => usedRoleSlugs.includes(g) || g === chosenCeo),
            kind: "service",
          });
        }
      } catch (err) {
        // The project exists either way — send them to Settings to finish.
        alert(`Project created, but storing a credential failed: ${(err as Error).message}. ` +
              "Add it in Settings → Service credentials.");
      }
      // Shape was checked above; this asks the harder question — does anything
      // answer at that address? A source that cannot answer is a project whose
      // agents will burn runs hunting for absent tools, so it does not start.
      const unreachable: string[] = [];
      // OAuth sources are the one kind that cannot finish here: the provider's
      // sign-in needs a project to come back to, and it exists only now. They
      // are saved with their wiring and connected from Settings, one click.
      const needSignIn: string[] = [];
      for (const source of savedSources) {
        try {
          const saved = await post<{
            check: { ok: boolean; detail: string };
            needs_connect?: boolean;
          }>(`/admin/projects/${created.id}/mcp_servers`, source.body);
          if (saved.needs_connect) needSignIn.push(source.name);
          else if (!saved.check.ok) unreachable.push(`"${source.name}": ${saved.check.detail}`);
        } catch (err) {
          unreachable.push(`"${source.name}": ${(err as Error).message}`);
        }
      }
      for (const source of new Set(dataSourceUrls.values())) {
        try {
          const check = await post<{ ok: boolean; detail: string }>(
            `/admin/projects/${created.id}/mcp_servers/${source}/check`, {});
          if (!check.ok) unreachable.push(`"${source}": ${check.detail}`);
        } catch (err) {
          unreachable.push(`"${source}": ${(err as Error).message}`);
        }
      }
      if (unreachable.length || needSignIn.length) {
        const lines = [
          ...unreachable.map((detail) => `not answering — ${detail}`),
          ...needSignIn.map((name) => `"${name}": waiting on its provider sign-in`),
        ];
        const subject = lines.length > 1 ? "some of its data sources are" : "a data source is";
        alert(
          `Project created, but ${subject} not ready:\n\n${lines.join("\n")}\n\n` +
            "The project was NOT started. Finish them in Settings → Data sources — that " +
            "form tests an address and holds the Connect button — then start the project.",
        );
        if (created.webhooks) setDone(created.id);
        else navigate(`/projects/${created.id}/settings?s=data`);
        return;
      }
      // The chairman already explained all of this to the Founder and argued
      // it through. Making them say it again to a new CEO is exactly the form
      // behaviour the Founder exists to remove.
      if (foundingThreadId) {
        try {
          await post(`/admin/founder/threads/${foundingThreadId}/attach`,
                     { project_id: created.id });
        } catch {
          /* the project exists; a missing briefing is not worth failing on */
        }
      }
      if (startNow && canStartNow) await post(`/admin/projects/${created.id}/start`);
      if (created.webhooks) setDone(created.id);
      else navigate(`/projects/${created.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // The project exists; what this screen still owes the chairman is the
  // webhook secrets, shown exactly once with the endpoint beside each — and
  // the names of the Slack rooms it just made, which are no use to anyone
  // sitting in a log file.
  if (done && webhooks) {
    const origin = window.location.origin;
    return (
      <>
        <h1>Project created</h1>
        {slack && (
          <div className="card" style={{ maxWidth: 760 }}>
            <p style={{ marginTop: 0 }}>
              <b>Your Slack boardroom.</b> This project inherited the app-wide Slack
              connection, so the platform made the room you talk to the CEO in and invited
              you to it. It mirrors the bus within seconds. Leadership and the team rooms
              are not made yet — one button in Settings makes them when you want them.
            </p>
            <table>
              <tbody>
                {slack.channels.map((room) => (
                  <tr key={room.key}>
                    <td style={{ width: "30%" }}>{room.label}</td>
                    <td>
                      {room.error
                        ? <span className="error">{room.error}</span>
                        : <><code>#{room.name}</code>{" "}
                            <span className="help">
                              {room.created ? "created" : "already existed — adopted"}
                            </span></>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {slack.deferred && slack.deferred.length > 0 && (
              <p className="help">
                Not made yet:{" "}
                {slack.deferred.map((room, i) => (
                  <span key={room.key}>
                    {i > 0 ? ", " : ""}<code>#{room.name}</code>
                  </span>
                ))}
                . <b>Create missing channels</b> in Settings → Integrations → Slack makes
                them all, whenever this project is ready to be run from Slack.
              </p>
            )}
            <p className="help">
              {slack.ok
                ? "Change or remap any of these in Settings → Integrations → Slack."
                : "Some rooms did not get made. Settings → Integrations → Slack has a "
                  + "button that retries only the missing ones."}
            </p>
          </div>
        )}
        <div className="card" style={{ maxWidth: 760 }}>
          <p style={{ marginTop: 0 }}>
            <b>Inbound evidence endpoints — optional, skippable.</b> Use these when an
            outside system should <em>prove</em> something happened: GitHub proving code
            merged, Stripe proving money landed, your app proving a signup. Evidence
            arriving signed with these secrets is platform-attested — proof no agent can
            forge, because the secrets never enter a sandbox. Most projects need none of
            this on day one; everything here is also in Settings → Integrations.
          </p>
          <table>
            <tbody>
              <tr>
                <td>GitHub</td>
                <td className="mono" style={{ wordBreak: "break-all" }}>
                  {origin}{webhooks.github.url}</td>
              </tr>
              <tr>
                <td>&nbsp;&nbsp;secret</td>
                <td className="mono" style={{ userSelect: "all", wordBreak: "break-all" }}>
                  {webhooks.github.secret}</td>
              </tr>
              <tr>
                <td>Generic</td>
                <td className="mono" style={{ wordBreak: "break-all" }}>
                  {origin}{webhooks.generic.url}</td>
              </tr>
              <tr>
                <td>&nbsp;&nbsp;secret</td>
                <td className="mono" style={{ userSelect: "all", wordBreak: "break-all" }}>
                  {webhooks.generic.secret}</td>
              </tr>
            </tbody>
          </table>
          <p className="help">
            GitHub signs with HMAC-SHA256 in <code>X-Hub-Signature-256</code> (paste the
            secret into the repo&apos;s webhook settings). Any other sender signs the raw
            JSON body into <code>X-Yaaf-Signature</code>, for example:
          </p>
          <pre className="mono" style={{ fontSize: 12, overflowX: "auto" }}>
{`BODY='{"description":"invoice 0042 paid","revenue_usd":29.0}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "<generic secret>" | cut -d' ' -f2)
curl -X POST "${origin}${webhooks.generic.url}" \\
  -H "Content-Type: application/json" -H "X-Yaaf-Signature: $SIG" -d "$BODY"`}
          </pre>
          <p className="help">
            Both secrets stay readable (and regenerable) in Settings → Integrations.
          </p>
          <button className="primary" onClick={() => navigate(`/projects/${done}`)}>
            Open the project
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <Link className="crumb" to="/projects/new">&larr; Both ways to start</Link>
          <h1>Fill it in myself</h1>
          <p className="subtitle" style={{ marginBottom: 0 }}>
            A small project that works while you're away. You can change everything
            afterwards except its shape.
          </p>
        </div>
        {/* The wizard is a view, not a replacement: same state, same submit. */}
        <div className="viewtog" role="group" aria-label="View">
          <button type="button" className={onePage ? "" : "on"}
            onClick={() => setView(false)}>Guided</button>
          <button type="button" className={onePage ? "on" : ""}
            onClick={() => setView(true)}>All on one page</button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="wiz">
        <nav className={`steprail${onePage ? " flat" : ""}`} aria-label="Steps">
          {STEPS.map((s) => {
            const problems = problemsOn(s.n);
            return (
              <button type="button" key={s.n} disabled={onePage}
                className={(!onePage && step === s.n ? "on " : "")
                  + (!onePage && s.n < step ? "done" : "")}
                aria-current={!onePage && step === s.n}
                onClick={() => setStep(s.n)}>
                <span className="sn">
                  <i>{!onePage && s.n < step ? "✓" : s.n}</i>{s.name}
                  {problems && <span className="sw" title={problems}>●</span>}
                </span>
                <span className="sd">{s.detail}</span>
              </button>
            );
          })}
        </nav>
      <form onSubmit={submit} className="stepbody">
        {on(1) && <>
        <div className="card" style={{ marginBottom: 10 }} ref={fileCard}>
          <label className="field">Start from a setup file (optional)</label>
          <p className="help" style={{ marginTop: 0 }}>
            Downloaded from a project's Settings → Setup file. Everything below prefills — review
            it, pick a slug, enter credential values, then create.
          </p>
          <input type="file" accept=".json,application/json" onChange={loadBundle} />
          {bundle && (
            <p className="help">
              Loaded "{bundle.project.name}" (exported from {bundle.source_slug}).
              {bundle.secrets.length > 0 &&
                (bundle.values_included
                  ? " Credential values came along — everything below is filled in; review and adjust."
                  : " Credential names came along without their values — fill them in below, or skip and add them later in Settings.")}
              {dataSourceUrls.size > 0 &&
                ` ${dataSourceUrls.size === 1 ? "One of them is a data source's" : "Some of them are data sources'"} ` +
                  "address: a full URL, tested before this project starts."}
              {" "}
              {billingCredentialId.trim()
                ? "It also named the credential that pays — selected in the Billing step."
                : bundle.secrets.some((s) => s.kind === "billing" && s.value)
                  ? "It carries a billing key value from before credentials were managed; that value is not imported. Add it under Settings → LLM credentials, then pick it in the Billing step."
                  : bundle.project.billing_secret_name
                  ? `It named a credential called "${bundle.project.billing_secret_name}", which this install does not have — pick one in the Billing step.`
                  : "It does not say which credential pays — pick one in the Billing step, or the project is created unable to start."}
            </p>
          )}
        </div>
        <div className="card">
          <div className="row">
            <div>
              <label className="field">Name</label>
              <input
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  if (!slugEdited && !bundle) setSlug(dedupedSlug(slugify(e.target.value)));
                }}
                required
              />
            </div>
            <div>
              <label className="field">Slug — picked for you from the name</label>
              <input
                value={slug}
                onChange={(e) => { setSlug(e.target.value); setSlugEdited(true); }}
                pattern="[a-z0-9\-]{2,64}"
                required
              />
              <div className="help" style={{ marginTop: 4 }}>
                a-z, 0-9, dashes. Deduped against existing projects; edit it if you care.
              </div>
            </div>
          </div>
          <div className="row">
            <div>
              <label className="field">Chairman email (you)</label>
              <input
                type="email"
                value={chairmanEmail}
                onChange={(e) => setChairmanEmail(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="field">Chairman display name</label>
              <input value={chairmanName} onChange={(e) => setChairmanName(e.target.value)} />
            </div>
          </div>
          <p className="help" style={{ marginTop: 4 }}>
            Your identity on approvals and boardroom messages — remembered for the next
            project. If SMTP is configured on the server (<code>YAAF_SMTP_HOST</code> in{" "}
            <code>deploy/.env</code>), needs, assists and budget alerts also email you here;
            without it, nothing is sent and this is identity only.
          </p>
        </div>


        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">
            Goal — what this project exists to achieve
          </label>
          <p className="help" style={{ margin: "0 0 6px" }}>
            The CEO plans from this and nothing else, so it is worth more of your
            time than everything below it put together. Brief it the way you would
            brief a competent human running the project for you.
          </p>
          <div className="goalwrap">
            <textarea value={goal} onChange={(e) => setGoal(e.target.value)}
              required minLength={10} className="goalbox"
              placeholder="Audit the customer's Amazon PPC account and produce a plain-English deck a non-technical business owner can act on: where money is being wasted, which campaigns to fix first, and expected savings. The account data comes from the connected &quot;ppc&quot; data source. Final deck by August 20th." />
            <div className="brief">
              <div className="bt">Does your brief say…</div>
              <ul>
                {briefChecks(goal).map((c) => (
                  <li key={c.label} className={c.ok ? "ok" : ""}>
                    <b>{c.ok ? "✓" : "○"}</b>{c.label}
                  </li>
                ))}
              </ul>
              <p className="help" style={{ marginTop: 8 }}>
                Advisory only — you can create with none of these ticked. A weak
                goal ("analyze the PPC data") gets you a project that putters.
              </p>
              {/* The other way in, offered at the one moment it is actually
                  wanted. A chairman who cannot get four ticks on his own brief
                  is exactly who the Founder was built for, and the checklist
                  above is where he finds that out — not the sidebar he read
                  ten minutes ago. */}
              <p className="help" style={{ marginTop: 8 }}>
                Stuck on the wording?{" "}
                <Link to="/projects/new/describe">
                  Describe it and let yaaf draft the whole project
                </Link>{" "}
                — it comes back here, filled in.
              </p>
            </div>
          </div>
        </div>

        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">What kind of project is this?</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 10, marginTop: 6 }}>
            {MODES.map((m) => (
              <button key={m.value} type="button"
                onClick={() => { setMode(m.value); setModeNotice(null); }}
                style={{
                  font: "inherit", textAlign: "left", cursor: "pointer",
                  background: mode === m.value ? "rgba(57,135,229,0.07)" : "var(--surface)",
                  border: mode === m.value
                    ? "1px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: 8, padding: "12px 14px",
                }}>
                <div style={{ fontWeight: 650, fontSize: 15, color: "var(--ink)" }}>
                  {mode === m.value ? "◉" : "○"} {m.label}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 6 }}>{m.blurb}</div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8, fontStyle: "italic",
                  borderTop: "1px solid var(--grid, var(--border))", paddingTop: 7 }}>
                  {m.example}
                </div>
              </button>
            ))}
          </div>
          {modeNotice && (
            <p className="help" style={{ marginTop: 10, color: "var(--warn, var(--ink-2))" }}>
              {modeNotice}
            </p>
          )}
          {mode !== "explore" ? (
            <p className="help" style={{ marginTop: 10 }}>
              This project starts owing a plan: the CEO proposes requirements,
              {autonomy === "autonomous"
                ? " accepts them itself under the authority you grant below,"
                : " you accept them on the Plan tab,"}{" "}
              and no work may be created until the plan holds and adds up against the
              budget.
            </p>
          ) : (
            <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "baseline",
              flexWrap: "wrap" }}>
              <label className="field" style={{ margin: 0 }}>
                Window (optional)
              </label>
              <input type="date" value={exploreDeadline}
                onChange={(e) => setExploreDeadline(e.target.value)}
                style={{ width: 170 }} />
              <span className="help">
                Open scope, bounded time — “one sale in the next 60 days.” When it
                passes you are told and the judgment is yours; nothing pauses itself.
              </span>
            </div>
          )}
        </div>
        </>}

        {on(2) && <>
        <h2>The project</h2>
        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">Who runs it — the only agent that speaks to you</label>
          <p className="help" style={{ margin: "0 0 8px" }}>
            The CEO owns the plan and is the only agent that creates top-level
            work. Frozen once the project starts.
          </p>
          <CeoPicker roles={roles} value={ceoSlug} onChange={setCeoSlug} />
        </div>
        <h2>Teams</h2>
        {missingRoleSlugs.length > 0 && (
          <p className="help" style={{ color: "var(--warn, var(--ink-2))" }}>
            This setup file hires {missingRoleSlugs.length === 1 ? "a role" : "roles"} this
            install does not have: <strong>{missingRoleSlugs.join(", ")}</strong>. The examples
            hire from the role library by slug rather than carrying their prompts, so a role
            added to the repository after this install was seeded is not here yet. Load the
            missing ones from your checkout — <code>./update_single_server.sh</code>, or{" "}
            <code>yaaf seed-roles roles/ --only-missing</code> — then reload this page and load
            the file again. Until then, pick a role this install does have for each team.
            {missingRoleSlugs.includes(bundleCeoSlug) && (
              <>
                {" "}<strong>{bundleCeoSlug}</strong> is the CEO's role, which this form has no
                picker for — the project cannot be created until the library has it.
              </>
            )}
          </p>
        )}
        {solo && (
          <p className="subtitle">
            This setup file is a solo project: the CEO is the only team's sole member, so the
            employee fields stay empty.
          </p>
        )}
        {teams.map((team, i) => (
          <div className="card" key={i} style={{ marginBottom: 10 }}>
            <div className="row">
              <div>
                <label className="field">Role</label>
                <select
                  value={team.role_slug}
                  onChange={(e) => setTeam(i, { role_slug: e.target.value })}
                  hidden
                  aria-hidden="true"
                >
                  <option value={team.role_slug}>{team.role_slug}</option>
                </select>
                <button type="button" className="rolepick"
                  onClick={() => setHireOpen(i)}>
                  {team.role_slug
                    ? (rolesLoaded && !known(team.role_slug)
                      // The role the file asked for, when this install has
                      // never heard of it: named rather than dropped, so the
                      // ask stays visible and submit can refuse it by name.
                      ? `${team.role_slug} — not in this install's role library`
                      : roleLabel(team.role_slug))
                    : "Choose a role…"}
                </button>
                {team.role_slug && known(team.role_slug) && (
                  <div className="help" style={{ marginTop: 4 }}>
                    {(() => {
                      const r = roles.find((x) => x.slug === team.role_slug);
                      const needs = needsOf(r?.secret_scopes ?? []);
                      return needs.length
                        ? `Needs ${needs.map((n) => n.words).join(" and ")}.`
                        : "Needs no outside key.";
                    })()}
                  </div>
                )}
              </div>
              <div>
                <label className="field">Team name</label>
                <input value={team.name} onChange={(e) => setTeam(i, { name: e.target.value })} />
              </div>
            </div>
            <div className="row">
              <div>
                <label className="field">Employees (comma-separated names)</label>
                <input
                  value={team.employees}
                  onChange={(e) => setTeam(i, { employees: e.target.value })}
                  placeholder="Ada, Lin"
                />
              </div>
              <div>
                <label className="field">Lead (defaults to first; a sole employee leads itself)</label>
                <input value={team.lead} onChange={(e) => setTeam(i, { lead: e.target.value })} />
              </div>
            </div>
            <div className="actions">
              <button
                type="button"
                className="small danger"
                onClick={() => setTeams((prev) => prev.filter((_, j) => j !== i))}
                disabled={teams.length === 1}
              >
                Remove team
              </button>
            </div>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setTeams((prev) => [...prev, { role_slug: "", name: "", employees: "", lead: "" }])}
        >
          + Add team
        </button>

        </>}

        {on(3) && <>
        <div className="card" style={{ marginTop: 10 }}>
          <CredentialPicker value={billingCredentialId} onChange={(id, mode) => { billingPick.current++; setBillingCredentialId(id); setBillingMode(mode); }} />
          <p className="help">The selected credential determines the budget unit. Without a credential, the project can be saved as a draft but cannot run models.</p>
          <button type="button" className="small" style={{ marginTop: 4 }}
            onClick={() => setShowAdvanced((v) => !v)}>
            {showAdvanced ? "hide advanced" : "advanced…"}
          </button>
          {showAdvanced && (
            <div style={{ marginTop: 8, maxWidth: 380 }}>
              <label className="field">Runtime</label>
              <select value={runtime} onChange={(e) => setRuntime(e.target.value)}>
                <option value="agent_sdk">agent_sdk — real model in the sandbox</option>
                <option value="mock">mock — scripted, $0 (CI / tire-kicking)</option>
              </select>
              <p className="help">
                Mock runs no model and needs no key — for testing the platform itself.
              </p>
            </div>
          )}
        </div>

        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">Who runs it?</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 10, marginTop: 6 }}>
            {AUTONOMY_PRESETS.map((p) => (
              <button key={p.value} type="button"
                onClick={() => { setAutonomy(p.value); setAutonomyNotice(null); }}
                style={{
                  font: "inherit", textAlign: "left", cursor: "pointer",
                  background: autonomy === p.value ? "rgba(57,135,229,0.07)" : "var(--surface)",
                  border: autonomy === p.value
                    ? "1px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: 8, padding: "12px 14px",
                }}>
                <div style={{ fontWeight: 650, fontSize: 15, color: "var(--ink)" }}>
                  {autonomy === p.value ? "◉" : "○"} {p.label}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 6 }}>{p.blurb}</div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8, fontStyle: "italic",
                  borderTop: "1px solid var(--grid, var(--border))", paddingTop: 7 }}>
                  {p.example}
                </div>
              </button>
            ))}
          </div>
          {autonomyNotice && (
            <p className="help" style={{ marginTop: 10, color: "var(--warn, var(--ink-2))" }}>
              {autonomyNotice}
            </p>
          )}
          <p className="help" style={{ marginTop: 10 }}>
            A preset is five rules in the constitution's Autonomy group — each one editable
            individually later in Settings → Rules in force, with a written reason, binding
            from the next agent call. Turning the dial up or back is always yours.
          </p>
        </div>
        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">How does it run?</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 10, marginTop: 6 }}>
            {RUN_PRESETS.map((p) => (
              <button key={p.value} type="button"
                onClick={() => applyRunPreset(p.value)}
                style={{
                  font: "inherit", textAlign: "left", cursor: "pointer",
                  background: runPreset === p.value ? "rgba(57,135,229,0.07)" : "var(--surface)",
                  border: runPreset === p.value
                    ? "1px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: 8, padding: "12px 14px",
                }}>
                <div style={{ fontWeight: 650, fontSize: 15, color: "var(--ink)" }}>
                  {runPreset === p.value ? "◉" : "○"} {p.label}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 6 }}>{p.blurb}</div>
              </button>
            ))}
          </div>
          {needsReviewSeat && (
            <div style={{ marginTop: 10, padding: "10px 12px", borderRadius: 8,
              border: "1px solid var(--warn, var(--border))",
              background: "rgba(229,158,57,0.07)", fontSize: 13 }}>
              This preset asks for the work to be read by somebody who did not write it,
              and no team on your roster does that — so the only reader of a deliverable
              before you would be the agent that wrote it. Creating it will be refused.
              {reviewRoles.length ? (
                <>
                  {" "}
                  <button type="button" className="small" onClick={addReviewTeam}
                    style={{ marginTop: 8 }}>
                    add a Review team ({(reviewRoles.find((r) => r.slug === "editor")
                      ?? reviewRoles[0]).name})
                  </button>
                </>
              ) : (
                " This install has no reviewing role installed, so there is nobody to "
                + "hire — seed the role library, or turn the read off here."
              )}
              {" "}
              <button type="button" className="small" style={{ marginTop: 8 }}
                onClick={() => setCraft((prev) => ({ ...prev, review: "none" }))}>
                turn the read off for this project
              </button>
              {/* The escape has to be IN the form. The card sets this rule and
                  the form renders no field for it, so the only way out of the
                  refusal used to be editing the constitution after creating a
                  project the server would not create. */}
            </div>
          )}
          <p className="help" style={{ marginTop: 10 }}>
            Pace, money, proof and how finished the work has to be — a preset just fills
            the numbers in the Constitution section below. Adjust any of them there, now
            or later in Settings. Every preset asks for the work to be read by somebody
            who did not write it, which means the roster needs a reviewing seat; the
            {" "}<b>Full send</b> card also hires the writing and checking seats on the
            strongest model, and raises the per-run ceiling so a long piece survives.
          </p>
          <p className="help" style={{ marginTop: 6 }}>
            {/* The rehearsal's own argument, reused: do not invent a price, invert
                the question. This box knows what a run has cost it and cannot know
                what this project's runs will cost. */}
            {(() => {
              if (tokensMode) {
                return `Metered in tokens against the selected subscription: the cap is ` +
                  `${tokenCap.toLocaleString()} tokens a month, and the plan's own session ` +
                  "and weekly windows apply before it does.";
              }
              const perRun = usdCap / Math.max(1, queueCeiling * 8);
              const seen = status?.avg_usd_per_run;
              return seen
                ? `Your $${usdCap.toLocaleString()} cap holds if a run averages under ` +
                  `$${perRun.toFixed(2)}. Runs on this install have averaged ` +
                  `$${seen.toFixed(2)}.`
                : `Your $${usdCap.toLocaleString()} cap holds if a run averages under ` +
                  `$${perRun.toFixed(2)}. This install has never priced a run, so there ` +
                  "is nothing to check that against yet.";
            })()}
          </p>
        </div>
        <h2>Constitution (frozen at creation)</h2>
        <div className="card">
          <div className="row">
            <div>
              <label className="field">Queue ceiling (open team tasks)</label>
              <input
                type="number"
                min={1}
                value={queueCeiling}
                onChange={(e) => setQueueCeiling(+e.target.value)}
              />
            </div>
            <div>
              <label className="field">Drought window (hours without verified external evidence)</label>
              <input
                type="number"
                min={1}
                value={droughtHours}
                onChange={(e) => setDroughtHours(+e.target.value)}
              />
            </div>
          </div>
          <div className="row">
            <div>
              {tokensMode ? (
                <>
                  <label className="field">Monthly budget cap (tokens)</label>
                  <input type="number" min={0} step={1_000_000} value={tokenCap} onChange={(e) => setTokenCap(+e.target.value)} />
                  <div className="help">The selected credential is a subscription, so the budget is in tokens.</div>
                </>
              ) : (
                <>
                  <label className="field">Monthly budget cap (USD)</label>
                  <input type="number" min={0} value={usdCap} onChange={(e) => setUsdCap(+e.target.value)} />
                </>
              )}
            </div>
            <div>
              <label className="field">Approval threshold (USD per spend)</label>
              <input
                type="number"
                min={0}
                value={approvalThreshold}
                onChange={(e) => setApprovalThreshold(+e.target.value)}
              />
            </div>
          </div>
          <div className="row">
            <div>
              <label className="field">Hours between unprompted status reports</label>
              <input
                type="number"
                min={0}
                step={0.5}
                value={reportHours}
                onChange={(e) => setReportHours(+e.target.value)}
              />
              <div className="help" style={{ marginTop: 4 }}>0 = no cadence, report freely.</div>
            </div>
            <div>
              <label className="field">Only report during these hours (optional)</label>
              <input
                value={reportWindow}
                onChange={(e) => setReportWindow(e.target.value)}
                placeholder="09:00-20:00"
              />
            </div>
          </div>
          <div className="row">
            <div>
              <label className="field">Your timezone</label>
              <input
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="America/New_York"
              />
              <div className="help" style={{ marginTop: 4 }}>
                Guessed from your browser — every local time in the rules reads this zone.
              </div>
            </div>
            <div>
              <label className="field">External evidence must be verified</label>
              <label style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8 }}>
                <input
                  type="checkbox"
                  style={{ width: "auto" }}
                  checked={requireVerification}
                  onChange={(e) => setRequireVerification(e.target.checked)}
                />
                <span className="help">
                  On, evidence counts only once the platform fetches and checks it.
                </span>
              </label>
            </div>
          </div>
            <label className="field">
              Controlled domains (comma-separated) — evidence from these can never count as external
            </label>
            <input
              value={controlledDomains}
              onChange={(e) => setControlledDomains(e.target.value)}
              placeholder="myproject.example, github.com/myorg"
            />
            <div className="help" style={{ marginTop: 4 }}>
              Your own website, <code>github:your-account</code>. Agents must prove
              progress with evidence from outside the project, and anything on a
              property you control cannot count as outside.
            </div>
          <p className="help">
            {bundle
              ? "Everything else (external-evidence floor, wake caps, memory caps, refusal caps…) " +
                "comes from the setup file, and every rule is editable later in Settings."
              : "Everything else (external-evidence floor, wake caps, memory caps, refusal caps…) " +
                "takes the lana-derived defaults, and every rule is editable later in Settings."}
          </p>
        </div>

        </>}

        {on(4) && <>
        <h2>Review &amp; create</h2>
        <p className="subtitle">
          This is the project that will exist. Everything here is still editable —
          step back, or switch to <strong>All on one page</strong>.
        </p>
        <div className="card" style={{ marginBottom: 10 }}>
          {/* Name and address live here too, not only on step 1: a loaded setup
              file lands straight on this step, and picking a slug that does not
              collide is the one thing that user must still do. */}
          <div className="row">
            <div>
              <label className="field">Name</label>
              <input value={name} onChange={(e) => {
                setName(e.target.value);
                if (!slugEdited && !bundle) setSlug(dedupedSlug(slugify(e.target.value)));
              }} required />
            </div>
            <div>
              <label className="field">Address</label>
              <input value={slug} pattern="[a-z0-9\-]{2,64}" required
                onChange={(e) => { setSlug(e.target.value); setSlugEdited(true); }} />
              <div className="help" style={{ marginTop: 4 }}>
                {existingSlugs.includes(slug)
                  ? "A project already uses this address — pick another."
                  : "Deduped against your existing projects."}
              </div>
            </div>
          </div>
          <label className="field" style={{ marginTop: 12 }}>Goal</label>
          <div className="help" style={{ whiteSpace: "pre-wrap" }}>
            {goal.trim() || "Nothing written yet — the CEO plans from this and nothing else."}
          </div>
        </div>

        <div className="card" style={{ marginBottom: 10 }}>
          <div className="orgline">
            <strong>CEO</strong>
            <span className="chip accent">{roleLabel(bundle?.project.ceo_role_slug ?? ceoSlug)}</span>
            <span className="help">the only agent that speaks to you</span>
          </div>
          {teams.filter((t) => t.role_slug && t.name).map((t, i) => (
            <div className="orgline" key={i}>
              <strong>{t.name}</strong>
              <span className="chip">{roleLabel(t.role_slug)}</span>
              <span className="help">
                {t.employees.trim() || (solo ? "the CEO, working alone" : "nobody yet")}
              </span>
            </div>
          ))}
          <div className="orgline">
            <strong>Shape</strong>
            <span className="chip">{MODES.find((m) => m.value === mode)?.label ?? mode}</span>
            <span className="help">{MODES.find((m) => m.value === mode)?.blurb}</span>
          </div>
          <div className="orgline">
            <strong>Cap</strong>
            {tokensMode ? (
              <>
                <span className="chip">{tokenCap.toLocaleString()} tokens/mo</span>
                <span className="help">
                  metered against the selected subscription — the project pauses itself at
                  the cap, and the plan's own windows apply before it
                </span>
              </>
            ) : (
              <>
                <span className="chip">${usdCap}/mo</span>
                <span className="help">
                  ≈ ${roughMonthly(hiredRoles)}/mo at this staffing — the project pauses
                  itself at the cap
                </span>
              </>
            )}
          </div>
        </div>

        {/* What it needs before it can start, derived from who was actually
            hired. The roles have always declared this; nothing ever showed it,
            so the first sign of a missing key was a run that failed asking for
            one. Every row is a real credential you can still rename, re-scope
            or remove below. */}
        <h2>Before it can start</h2>
        <div className="card" style={{ marginBottom: 10 }}>
          <div className="needline">
            <span>
              <strong>Billing credential</strong>
              <div className="help">Agents cannot run without one.</div>
            </span>
            <span>
              {billingCredentialId.trim()
                ? <span className="badge good">connected above</span>
                : <span className="badge warn">add it on the previous step, or in Settings</span>}
            </span>
          </div>
          {requiredScopes.length === 0 ? (
            <div className="needline">
              <span className="help">
                Nothing else — none of your hires needs an outside key.
              </span>
              <span />
            </div>
          ) : requiredScopes.map((need) => {
            const filled = credentials.some(
              (c) => c.name.trim() === need.scope && c.value.trim());
            return (
              <div className="needline" key={need.scope}>
                <span>
                  <strong>{need.words}</strong>
                  <div className="help">
                    goes to {need.roles.join(" · ")}
                  </div>
                </span>
                <span>
                  {filled
                    ? <span className="badge good">filled in below</span>
                    : <span className="badge warn">add it below, or in Settings later</span>}
                </span>
              </div>
            );
          })}
        </div>

        <h2>Service credentials (optional)</h2>
        <p className="subtitle">
          Keys your agents will need for outside services (WordPress, GitHub, Stripe…). Encrypted
          at rest and injected only into the sandboxes of roles you tick — agents are told the
          variable's name, never shown the value. You can also add these later in Settings.
        </p>
        {credentials.map((cred, i) => {
          const source = dataSourceUrls.get(cred.name.trim());
          const badUrl = source !== undefined && !!cred.value.trim() && !isFullUrl(cred.value);
          return (
          <div className="card" key={i} style={{ marginBottom: 10 }}>
            <div className="row">
              <div>
                <label className="field">Name</label>
                <input
                  value={cred.name}
                  onChange={(e) => setCredential(i, { name: e.target.value })}
                  placeholder="wordpress, github-token…"
                />
                {cred.name.trim() && (
                  <div className="help" style={{ marginTop: 4 }}>
                    injected as <code>${envVarName(cred.name)}</code>
                  </div>
                )}
              </div>
              <div>
                <label className="field">
                  {source ? `Address of data source "${source}"` : "Value"}
                </label>
                <SecretInput
                  // An address is not a password: showing it is how a typo gets
                  // seen, and it is the one credential the server can judge.
                  defaultShown={!!source}
                  value={cred.value}
                  onChange={(value) => setCredential(i, { value })}
                  placeholder={source ? "https://host:3845/mcp" : "the key itself"}
                />
                {source && (
                  <div className={badUrl ? "error" : "help"} style={{ marginTop: 4 }}>
                    {badUrl
                      ? "That is not a full address — scheme included, like " +
                        "https://host:3845/mcp. A bare hostname or a token mounts nothing."
                      : cred.value.trim()
                        ? "Tested for real before the project starts."
                        : `Left blank, "${source}" mounts no tools — and the roles granted it ` +
                          "only find out mid-run."}
                  </div>
                )}
              </div>
            </div>
            <label className="field">Which roles receive it (nobody, until you grant)</label>
            {usedRoleSlugs.length === 0 ? (
              <p className="help">Pick your teams' roles above first.</p>
            ) : (
              <div className="pill-row">
                {usedRoleSlugs.map((slug) => (
                  <label key={slug} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      style={{ width: "auto" }}
                      checked={cred.grants.includes(slug)}
                      onChange={() => toggleGrant(i, slug)}
                    />
                    {roleLabel(slug)}
                  </label>
                ))}
              </div>
            )}
            <div className="actions">
              <button
                type="button"
                className="small danger"
                onClick={() => setCredentials((prev) => prev.filter((_, j) => j !== i))}
              >
                Remove credential
              </button>
            </div>
          </div>
          );
        })}
        <button
          type="button"
          onClick={() => setCredentials((prev) => [...prev, { name: "", value: "", grants: [] }])}
        >
          + Add credential
        </button>

        <h2>Data sources (optional)</h2>
        <DataSourceIntro>
          One added here is saved with the project and tested again before it starts.
          Afterwards, sources are added and changed in Settings → Data sources.
        </DataSourceIntro>
        {dataSources.map((source, i) => (
          <div className="card" key={i} style={{ marginBottom: 10 }}>
            <DataSourceFields
              value={source}
              onChange={(next) => setDataSource(i, next)}
              roleOptions={roleOptions}
              rolesLabel="Which roles get this source (nobody, until you tick)"
              rolesEmptyHint="Pick your teams' roles above first."
              oauthNote={
                "For hosted servers like Notion, Linear or Atlassian. The provider's " +
                "sign-in cannot happen until the project exists, so this one is saved " +
                "unconnected: finish it with Connect in Settings → Data sources, and the " +
                "project waits rather than starting agents without the tools."
              }
            />
            <div className="actions">
              <button
                type="button"
                className="small danger"
                onClick={() => setDataSources((prev) => prev.filter((_, j) => j !== i))}
              >
                Remove data source
              </button>
            </div>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setDataSources((prev) => [...prev, { ...BLANK_DATA_SOURCE }])}
        >
          + Add data source
        </button>

        {/* The drill. It sits above the create button because that is the
            order the question comes in: a chairman about to hand over a budget
            wants to know the thing works, and until now the only way to find
            out was to hand over the budget. */}
        <h2>Try it for nothing first</h2>
        <div className="card" style={{ marginBottom: 10 }}>
          <div className="hire-foot">
            <button type="button" onClick={rehearse} disabled={drilling}>
              {drilling ? "Running the drill…" : "Run a fire drill"}
            </button>
            <span className="help">
              Runs this exact spec as a project on scripted agents — no model calls,
              no money, nothing created. Tells you whether it gets off the ground,
              how often it will stop and wait for you, and what paces it.
            </span>
          </div>
          {drillError && <div className="error" style={{ marginTop: 10 }}>{drillError}</div>}
          {drill && <Drill report={drill} />}
        </div>

        <div className="actions">
          <label style={{ display: "flex", gap: 6, alignItems: "center",
            opacity: canStartNow ? 1 : 0.55 }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={startNow && canStartNow}
              disabled={!canStartNow}
              onChange={(e) => setStartNow(e.target.checked)}
            />
            Start immediately
            {!canStartNow && (
              <span className="help">— needs the billing key above (agents cannot run keyless)</span>
            )}
          </label>
          <div className="spacer" />
          <button className="primary" disabled={busy}>
            {busy ? "Creating…" : "Create project"}
          </button>
        </div>
        </>}
        {hireOpen !== null && (
          <HirePanel
            roles={roles}
            crews={crews}
            loaded={rolesLoaded}
            selected={teams[hireOpen]?.role_slug ? [teams[hireOpen].role_slug] : []}
            goal={goal}
            onToggle={(slug) => {
              const i = hireOpen;
              setTeams((prev) => prev.map((t, j) => {
                if (j !== i) return t;
                if (t.role_slug === slug) return { ...t, role_slug: "" };
                // Name the team after the role unless the chairman named it.
                const named = t.name.trim()
                  ? t.name
                  : (roles.find((r) => r.slug === slug)?.name.split(" / ")[0] ?? "");
                return { ...t, role_slug: slug, name: named };
              }));
              setHireOpen(null);
            }}
            onApplyCrew={(crew) => {
              // A crew fills the team list and stays fully editable — the
              // employees, leads and schedules come from the worked example
              // it was lifted from, so it lands as a project rather than a
              // list of job titles.
              setTeams(crew.teams.map((t) => ({
                role_slug: t.role_slug,
                name: t.name,
                employees: t.employees.join(", "),
                lead: t.lead ?? "",
                schedules: t.schedules,
              })));
              if (crew.ceo_role_slug) setCeoSlug(crew.ceo_role_slug);
              setHireOpen(null);
            }}
            onClose={() => setHireOpen(null)}
            title="Who's on this team?"
          />
        )}

        {!onePage && (
          <div className="stepnav">
            {step > 1 && (
              <button type="button" onClick={() => setStep(step - 1)}>← Back</button>
            )}
            <div className="spacer" />
            {step < 4 && (
              <button type="button" className="primary" onClick={() => setStep(step + 1)}>
                Next — {STEPS[step].name} →
              </button>
            )}
          </div>
        )}
      </form>
      </div>
    </>
  );
}
