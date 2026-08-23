import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, McpServerEntry, patch, post, ProjectBundle, RoleSummary } from "../api";
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
import { SecretInput } from "../ui";

interface TeamDraft {
  role_slug: string;
  name: string;
  employees: string; // comma-separated names
  lead: string;
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
const AUTONOMY_PRESETS = [
  { value: "supervised", label: "Supervised",
    blurb: "You approve scope, sign the date, and judge unverifiable closes. " +
      "The CEO proposes — and executes any order you give it in the boardroom.",
    example: "Today's behavior. Right for a first project, or real money on the line.",
    constitution: { plan_authority: "chairman", scope_authority: "chairman",
      commitment_authority: "chairman", verdict_policy: "chairman",
      capability_authority: "chairman" } },
  { value: "autonomous", label: "Autonomous",
    blurb: "The CEO accepts its own plan, signs the platform's projected date, and " +
      "judges unverifiable closes itself. Every decision is audited and sent to " +
      "you; all of it is reversible; your buttons never close.",
    example: "The CEO runs the company; you chair it. Budgets and brakes still bind.",
    constitution: { plan_authority: "ceo", scope_authority: "ceo",
      commitment_authority: "ceo", verdict_policy: "ceo_judgment",
      capability_authority: "ceo" } },
] as const;

// How the company runs day to day: pace, money, proof. A preset is nothing
// but a bundle of ordinary constitution numbers — picking one fills the
// fields below, every field stays editable here and later in Settings →
// Rules in force. Deliberately orthogonal to the authority presets above:
// these never touch who holds the verbs.
const RUN_PRESETS = [
  { value: "full_send", label: "Full send",
    blurb: "Deliver as fast as possible; cost is secondary. Big budget, big queue, " +
      "hourly reports.",
    knobs: { queue: 16, drought: 72, cap: 5000, threshold: 1000, verify: false, report: 1 } },
  { value: "balanced", label: "Balanced",
    blurb: "Steady pace, a real budget, a report every hour. The default for a reason.",
    knobs: { queue: 8, drought: 24, cap: 1000, threshold: 250, verify: false, report: 1 } },
  { value: "frugal", label: "Slow & steady",
    blurb: "Small budget, small queue, a report every few hours. Right for a " +
      "long-running side project.",
    knobs: { queue: 4, drought: 48, cap: 200, threshold: 50, verify: false, report: 4 } },
  { value: "show_me_proof", label: "Show me proof",
    blurb: "External validation matters: evidence counts only once the platform " +
      "verifies it, and a proof drought blocks busywork.",
    knobs: { queue: 8, drought: 24, cap: 1000, threshold: 250, verify: true, report: 1 } },
] as const;

// Remembered across projects — there is one chairman on this install, and
// retyping your own email every time was pure friction.
const CHAIRMAN_KEY = "yaaf.chairman";

export default function NewProject() {
  const navigate = useNavigate();
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
  const [billingKey, setBillingKey] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [mode, setMode] = useState("explore");
  // Set when a loaded setup file could not answer the shape question itself —
  // the one prefill that must never be guessed silently, because a project's
  // mode is frozen at creation and Explore hides the Plan tab by design.
  const [modeNotice, setModeNotice] = useState<string | null>(null);
  const [exploreDeadline, setExploreDeadline] = useState("");
  const [autonomy, setAutonomy] = useState("supervised");
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
  const [queueCeiling, setQueueCeiling] = useState(8);
  const [droughtHours, setDroughtHours] = useState(24);
  const [usdCap, setUsdCap] = useState(1000);
  const [approvalThreshold, setApprovalThreshold] = useState(250);
  const [requireVerification, setRequireVerification] = useState(false);
  const [reportHours, setReportHours] = useState(1);
  const [reportWindow, setReportWindow] = useState("");
  const [timezone, setTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [startNow, setStartNow] = useState(true);

  // A setup file downloaded from a project's Settings. Loading one prefills
  // the whole form; the file's full constitution/config and any role
  // definitions the library lacks ride along at submit time.
  const [bundle, setBundle] = useState<ProjectBundle | null>(null);
  const [solo, setSolo] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [webhooks, setWebhooks] = useState<{
    projectId: string;
    github: { url: string; secret: string };
    generic: { url: string; secret: string };
  } | null>(null);

  useEffect(() => {
    get<RoleSummary[]>("/admin/roles")
      .then((rows) => { setRoles(rows); setRolesLoaded(true); })
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
  }

  const workerRoles = roles.filter((r) => !r.is_ceo_role);
  const ceoRole = roles.find((r) => r.is_ceo_role);

  function setTeam(i: number, patch: Partial<TeamDraft>) {
    setTeams((prev) => prev.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  }

  // Roles actually used by the drafted teams — the only sensible grant targets.
  const usedRoleSlugs = [...new Set(teams.map((t) => t.role_slug).filter(Boolean))];

  function setCredential(i: number, patch: Partial<CredentialDraft>) {
    setCredentials((prev) => prev.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  }

  function setDataSource(i: number, next: DataSourceDraft) {
    setDataSources((prev) => prev.map((s, j) => (j === i ? next : s)));
  }

  // Start needs a model to bill: a real key here, or the mock runtime. A
  // project can always be created without one and connected in Settings —
  // it just cannot START, because its first run would die hunting for a key.
  const canStartNow = runtime === "mock" || !!billingKey.trim();

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
          "company it was. Nothing was changed — confirm the choice above is what you want.",
        );
      }
      setRuntime(String(p.integrations["runtime"] ?? "agent_sdk"));
      setControlledDomains((p.controlled_domains ?? []).join(", "));
      setSolo(Boolean(p.solo));
      // Who ran the source project. A prefill like the rest of the form —
      // change it if the twin answers to someone else.
      if (p.chairman_email) setChairmanEmail(p.chairman_email);
      // An Explore window that is still open carries; a spent one would be
      // refused at create (a company the janitor closes at birth), so it is
      // left blank rather than prefilled into a guaranteed error.
      if (p.deadline && new Date(p.deadline) > new Date()) {
        setExploreDeadline(p.deadline.slice(0, 10));
      }
      // A full backup carries the billing key too — the one credential the
      // form holds apart, because it is attached rather than role-granted.
      const billing = parsed.secrets.find((s) =>
        p.billing_secret_name ? s.name === p.billing_secret_name : s.kind === "billing");
      if (billing?.value) setBillingKey(billing.value);
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
      const cons = p.constitution as Record<string, number>;
      if (cons.queue_ceiling !== undefined) setQueueCeiling(cons.queue_ceiling);
      if (cons.drought_hours !== undefined) setDroughtHours(cons.drought_hours);
      if (cons.monthly_usd_cap !== undefined) setUsdCap(cons.monthly_usd_cap);
      if (cons.approval_threshold_usd !== undefined) {
        setApprovalThreshold(cons.approval_threshold_usd);
      }
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

  function roleLabel(slug: string): string {
    return (
      workerRoles.find((r) => r.slug === slug)?.name ??
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
      const ceoSlug = bundle?.project.ceo_role_slug ?? ceoRole?.slug;
      if (!ceoSlug) throw new Error("No CEO role in the library — create one on the Roles page first.");
      await post("/admin/users", {
        email: chairmanEmail.trim(),
        display_name: chairmanName.trim() || chairmanEmail.trim(),
      });
      try {
        localStorage.setItem(CHAIRMAN_KEY, JSON.stringify(
          { email: chairmanEmail.trim(), name: chairmanName.trim() }));
      } catch { /* remembering is a convenience, never a requirement */ }
      const body = {
        slug: slug.trim(),
        name: name.trim(),
        goal: goal.trim(),
        chairman_email: chairmanEmail.trim(),
        billing_mode: billingMode,
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
          approval_threshold_usd: approvalThreshold,
          require_evidence_verification: requireVerification,
          chairman_report_interval_hours: reportHours,
          chairman_report_window_local: reportWindow.trim() || null,
          timezone: timezone.trim() || "UTC",
          ...(AUTONOMY_PRESETS.find((p) => p.value === autonomy)?.constitution ?? {}),
        },
        integrations: { ...(bundle?.project.integrations ?? {}), runtime },
        ...(bundle?.project.github_repo ? { github_repo: bundle.project.github_repo } : {}),
        ceo_role_slug: ceoSlug,
        ceo_name: bundle?.project.ceo_name ?? "CEO",
        ...(bundle?.project.ceo_daily_at ? { ceo_daily_at: bundle.project.ceo_daily_at } : {}),
        ...(bundle?.project.ceo_model_override
          ? { ceo_model_override: bundle.project.ceo_model_override } : {}),
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
            const schedules = keep(fromBundle?.schedules);
            const modelOverrides = keep(fromBundle?.model_overrides);
            return {
              role_slug: t.role_slug,
              name: t.name.trim(),
              employees,
              lead: t.lead.trim() || undefined,
              ...(Object.keys(schedules).length ? { schedules } : {}),
              ...(Object.keys(modelOverrides).length
                ? { model_overrides: modelOverrides } : {}),
            };
          }),
      };
      const created = await post<{
        id: string;
        webhooks?: { github: { url: string; secret: string };
                     generic: { url: string; secret: string } };
      }>("/admin/projects", body);
      if (created.webhooks) setWebhooks({ projectId: created.id, ...created.webhooks });
      // The model key, stored and attached in one motion — without it the
      // project exists but cannot start (its first run would die keyless).
      if (billingKey.trim()) {
        try {
          const stored = await post<{ id: string }>("/admin/secrets", {
            project_id: created.id,
            name: bundle?.project.billing_secret_name || "anthropic-api-key",
            value: billingKey.trim(),
            role_scopes: [],
            kind: "billing",
          });
          await patch(`/admin/projects/${created.id}`, { billing_secret_id: stored.id });
        } catch (err) {
          alert(`Project created, but attaching the model key failed: ` +
                `${(err as Error).message}. Connect it in Settings → Billing.`);
        }
      }
      try {
        for (const cred of credentials) {
          if (!cred.name.trim() || !cred.value) continue;
          await post("/admin/secrets", {
            project_id: created.id,
            name: cred.name.trim(),
            value: cred.value,
            role_scopes: cred.grants.filter((g) => usedRoleSlugs.includes(g) || g === ceoSlug),
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
      if (startNow && canStartNow) await post(`/admin/projects/${created.id}/start`);
      if (created.webhooks) setDone(created.id);
      else navigate(`/projects/${created.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // The project exists; the one thing this screen still owes the chairman is
  // the webhook secrets, shown exactly once with the endpoint beside each.
  if (done && webhooks) {
    const origin = window.location.origin;
    return (
      <>
        <h1>Project created</h1>
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
      <h1>New project</h1>
      <p className="subtitle">
        These are the limits your agents will run under. Set them here, and change any of them
        later in Settings → Rules in force — every change is recorded and takes effect at once.
      </p>
      {error && <div className="error">{error}</div>}
      <form onSubmit={submit} style={{ maxWidth: 760 }}>
        <div className="card" style={{ marginBottom: 10 }}>
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
              {bundle.values_included && billingKey.trim()
                ? "The billing key came along too — it is in the Billing card below."
                : "Billing is connected afterwards in Settings."}
            </p>
          )}
        </div>
        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">What kind of company is this?</label>
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
        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">Who runs it?</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 10, marginTop: 6 }}>
            {AUTONOMY_PRESETS.map((p) => (
              <button key={p.value} type="button"
                onClick={() => setAutonomy(p.value)}
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
          <p className="help" style={{ marginTop: 10 }}>
            Pace, money and proof — a preset just fills the numbers in the Constitution
            section below. Adjust any of them there, now or later in Settings.
          </p>
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
                pattern="[a-z0-9-]{2,64}"
                required
              />
              <div className="help" style={{ marginTop: 4 }}>
                a-z, 0-9, dashes. Deduped against existing projects; edit it if you care.
              </div>
            </div>
          </div>
          <label className="field">Goal — what this company exists to achieve</label>
          <textarea value={goal} onChange={(e) => setGoal(e.target.value)} required minLength={10} />
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
          <label className="field">
            Controlled domains (comma-separated) — evidence from these can never count as external
          </label>
          <input
            value={controlledDomains}
            onChange={(e) => setControlledDomains(e.target.value)}
            placeholder="myproject.example, github.com/myorg"
          />
        </div>

        <div className="card" style={{ marginTop: 10 }}>
          <label className="field">Billing — the key your agents think with</label>
          <div className="row">
            <div>
              <select value={billingMode} onChange={(e) => setBillingMode(e.target.value)}>
                <option value="api_key">Anthropic API key — budget in USD</option>
                <option value="subscription">Claude subscription token — budget in tokens</option>
              </select>
            </div>
            <div>
              <SecretInput
                value={billingKey}
                onChange={setBillingKey}
                placeholder={billingMode === "api_key"
                  ? "sk-ant-…" : "the OAuth token from `claude setup-token`"}
              />
            </div>
          </div>
          <p className="help">
            Stored encrypted, attached as this project's billing credential, never injected
            into a sandbox. Leave it blank and the project is created but cannot start —
            connect the key later in Settings.
          </p>
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
                >
                  <option value="">— pick a role —</option>
                  {workerRoles.map((r) => (
                    <option key={r.slug} value={r.slug}>
                      {r.name} ({r.model ?? "default"})
                    </option>
                  ))}
                  {newBundleRoles
                    .filter((r) => !r.is_ceo_role)
                    .map((r) => (
                      <option key={r.slug} value={r.slug}>
                        {r.name} — new, from the setup file
                      </option>
                    ))}
                  {/* The role the file asked for, when this install has never
                      heard of it. Held in the picker rather than dropped, so
                      the ask is visible and submit can refuse it by name. */}
                  {rolesLoaded && !known(team.role_slug) && (
                    <option value={team.role_slug}>
                      {team.role_slug} — not in this install's role library
                    </option>
                  )}
                </select>
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
              <label className="field">Monthly budget cap (USD)</label>
              <input type="number" min={0} value={usdCap} onChange={(e) => setUsdCap(+e.target.value)} />
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
          <p className="help">
            {bundle
              ? "Everything else (external-evidence floor, wake caps, memory caps, refusal caps…) " +
                "comes from the setup file, and every rule is editable later in Settings."
              : "Everything else (external-evidence floor, wake caps, memory caps, refusal caps…) " +
                "takes the lana-derived defaults, and every rule is editable later in Settings."}
          </p>
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
      </form>
    </>
  );
}
