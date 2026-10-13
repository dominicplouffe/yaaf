"""The runner daemon: leases jobs, executes them, survives their deaths.

Lease semantics: a queue row is claimed with FOR UPDATE SKIP LOCKED, the runs
row is created, and the queue row deleted — one transaction, so work is never
both queued and running, and two workers can never hold the same job. While a
job executes, a heartbeat thread keeps the lease fresh and watches for
revocation (kill switches, refusal caps); the janitor reaps anything whose
heartbeat goes stale.

Cost is booked even for killed and failed runs — a cost report that omits the
cost of producing it is wrong.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, prices
from yaaf.core.config import settings
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    LedgerEntry,
    LedgerKind,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Secret,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.core.tokens import mint
from yaaf.runner.backends import get_backend
from yaaf.runner.types import RunJob, RunResult
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS, credential_env, failure_markers
from yaaf.runtimes.seats import effective_harness, seat_for
from yaaf.server import policy
from yaaf.server.contextpack import build_context_pack, build_system_prompt, role_version_for
from yaaf.server.dispatcher import MODEL_RUN_KINDS, NON_WORK_RUN_KINDS

log = logging.getLogger("yaaf.runner")

MAX_PARALLEL = 4


def main() -> None:  # pragma: no cover - process entrypoint
    # Imported here, not at module top: chat imports this module's helpers.
    from yaaf.runner.chat import chat_supervisor_loop

    logging.basicConfig(level=logging.INFO)
    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
    log.info("runner %s starting", settings().worker_name)
    threading.Thread(target=chat_supervisor_loop, daemon=True).start()
    while True:
        leased = lease_and_prepare(limit=MAX_PARALLEL)
        for job in leased:
            pool.submit(execute_job, job)
        if not leased:
            time.sleep(settings().runner_poll_s)


# ── leasing ─────────────────────────────────────────────────────────────────────


def lease_and_prepare(limit: int = 1) -> list[RunJob]:
    """Claim up to `limit` queue rows and assemble everything their execution
    needs, in one transaction per row."""
    jobs: list[RunJob] = []
    with db_session() as session:
        now = utcnow()
        rows = list(
            session.execute(
                select(RunQueue)
                .where((RunQueue.not_before.is_(None)) | (RunQueue.not_before <= now))
                .order_by(RunQueue.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).scalars()
        )
        for queued in rows:
            project = session.get(Project, queued.project_id)
            if project is None or project.status != ProjectStatus.RUNNING:
                # Paused/stopped mid-queue. Dropping the row is correct and not
                # a lost wake: every wake reason is derived from durable state —
                # a ready task, an unconsumed notice, an unread message, a due
                # heartbeat — so the dispatcher re-decides this within one tick
                # of the project resuming. Holding the row instead would put a
                # paused project at the head of a queue shared by every project
                # on the box. It is logged because "the run I saw queued is
                # gone" deserves an answer somewhere.
                log.info("dropping queued %s for %s: project is %s", queued.kind,
                         queued.project_id,
                         project.status if project else "missing")
                session.delete(queued)
                continue
            job = _materialize(session, queued, project)
            if job is not None:
                jobs.append(job)
            session.delete(queued)
    return jobs


def _materialize(session: Session, queued: RunQueue, project: Project) -> RunJob | None:
    cons = effective(session, project.id)
    run = Run(
        id=queued.id,
        project_id=project.id,
        agent_id=queued.agent_id,
        kind=queued.kind,
        trigger=queued.trigger,
        task_id=queued.task_id,
        requirement_id=queued.requirement_id,
        attempt=queued.attempt,
        prior_run_id=queued.prior_run_id,
        status=RunStatus.RUNNING,
        worker=settings().worker_name,
        started_at=utcnow(),
        heartbeat_at=utcnow(),
    )
    session.add(run)
    session.flush()

    job = RunJob(
        run_id=run.id,
        project_id=project.id,
        agent_id=queued.agent_id,
        kind=queued.kind,
        trigger=queued.trigger or {},
        task_id=queued.task_id,
        requirement_id=queued.requirement_id,
        attempt=queued.attempt,
        timeout_s=cons.run_timeout_s,
        api_base_url=settings().api_base_url,
        workspace_dir=_prepare_workspace(session, project),
    )

    if queued.kind == RunKind.SUCCESS_CHECK:
        task = session.get(Task, queued.task_id)
        if task is None or task.status != TaskStatus.CHECKING:
            run.status = RunStatus.FAILED
            run.summary = "success check job with no task in checking state"
            run.finished_at = utcnow()
            return None
        job.check_command = (task.success_test or {}).get("command", "")
        job.timeout_s = min(cons.run_timeout_s, 300)
        return job

    if queued.kind == RunKind.REQUIREMENT_CHECK:
        requirement = session.get(Requirement, queued.requirement_id)
        if requirement is None or requirement.status != RequirementStatus.CHECKING:
            run.status = RunStatus.FAILED
            run.summary = "requirement check job with no requirement in checking state"
            run.finished_at = utcnow()
            return None
        job.check_command = (requirement.acceptance_test or {}).get("command", "")
        job.requirement_id = requirement.id
        job.timeout_s = min(cons.run_timeout_s, 300)
        return job

    agent = session.get(Agent, queued.agent_id)
    if agent is None or agent.status != "active":
        run.status = RunStatus.FAILED
        run.summary = "agent missing or paused at lease time"
        run.finished_at = utcnow()
        return None

    token_plain, token_hash = mint()
    run.token_hash = token_hash
    job.api_token = token_plain
    job.system_prompt = build_system_prompt(session, agent, project)
    job.context_pack = build_context_pack(session, run, agent, project)

    role_version = role_version_for(session, agent)
    job.max_usd = cons.max_usd_per_run
    job.task_budget_tokens = cons.run_token_budget
    problem = _seat_problem(session, project, agent)
    if problem:
        # Every door a seat comes through refuses this already (admin.
        # refuse_unrunnable_seats); this is the backstop for a door nobody has
        # built yet. Failed at lease, like a paused agent: no container, no
        # bill, and the reason on the Runs page instead of a dead sandbox.
        run.status = RunStatus.FAILED
        run.summary = f"seat cannot run: {problem}"
        run.finished_at = utcnow()
        return None
    from yaaf.core.project_credentials import project_lineup

    seat = seat_for(agent, role_version, project_lineup(session, project),
                    runtime=(project.integrations or {}).get("runtime"))
    _pin_seat(session, run, job, seat)
    job.tool_permissions = dict(role_version.tool_permissions) if role_version else {}
    if job.tool_permissions.get("web") and not seat.searches:
        job.system_prompt += FETCH_ONLY_PROMPT
    job.agent_kind = agent.kind
    source_specs = _merge_project_data_sources(session, project, role_version, job)
    role_secrets = _role_secret_env(session, project, role_version)
    job.billing_env = {**_billing_env(session, project, job.provider), **role_secrets}
    job.provider_spec = _provider_spec(session, project, job.provider)
    # Credentials are capabilities: each variable arrives with what it is and
    # how to use it, where the recognizer library knows (docs/AUTONOMY.md).
    from yaaf.core.naming import announced_credentials
    from yaaf.server.recognizers import credentials_prompt

    job.system_prompt += credentials_prompt(*announced_credentials(role_secrets))
    job.system_prompt += _data_source_prompt(source_specs, job.harness)
    return job


#: A `web` grant on a seat whose provider and harness cannot search (R2's
#: other half, decided: fetch only, and said). Said to the agent too, so it
#: does not spend its run looking for a search tool it was never handed.
FETCH_ONLY_PROMPT = (
    "\n\nWEB: you can fetch any page by its URL, but this seat has no web search — "
    "the model it runs on does not offer one. Work from addresses you know or "
    "can derive (official sites, documentation, APIs, sources already in the "
    "workspace), and say so in your result when an answer needed a search you "
    "could not run.\n"
)


def _seat_problem(session: Session, project: Project, agent: Agent) -> str | None:
    """The seat's own choice, checked as the doors check it — before the
    project's `runtime` override, which is a test switch, not a choice."""
    from yaaf.server.seat_checks import seat_problem

    problem = seat_problem(session, project, [agent], chat=False)
    # The doors name the seat; the run already does.
    return problem.removeprefix(f"{agent.name}: ") if problem else None


def _pin_seat(session: Session, run: Run, job: RunJob, seat) -> None:
    """What this run executes on, onto the job and the row. Pinned at lease
    time, not finalize: reaped and killed runs stay attributed to the model,
    provider and harness that actually ran — and priced at what the install
    priced it at when it ran, not at whatever the price is by finalize."""
    job.model, job.effort = seat.model, seat.effort
    job.provider, job.harness = seat.provider, seat.harness
    price = prices.price(session, seat.model)
    job.price = price.as_dict() if price else None
    row = catalog.model(seat.model)
    if row is not None:
        job.model_flags = {"effort": row.effort, "web_tools": row.web_tools}
    run.model, run.effort = seat.model, seat.effort
    run.provider, run.harness = seat.provider, seat.harness


def _data_source_prompt(specs: list[dict], harness: str | None = None) -> str:
    """The DATA SOURCES paragraph — what is connected, and how to use it without
    setting money on fire.

    The cost lesson is stated here rather than in the always-on prompt because
    it is only payable when a source exists: a run with nothing connected should
    not carry a paragraph about pulls it cannot make. Both lanes (scheduled runs
    and the resident chat) build it from the same specs. Tool names are the
    ones the seat's harness shows its model (`Harness.tool_ref`).
    """
    if not specs:
        return ""
    from yaaf.runtimes.harnesses import HARNESSES

    found = HARNESSES.get(harness or "")
    ref = found.tool_ref if found is not None else "mcp__{server}__{tool}"
    inline = [s.get("name", "?") for s in specs if s.get("access", "inline") != "bulk"]
    bulk = [s.get("name", "?") for s in specs if s.get("access", "inline") == "bulk"]
    lines = ["\n\nDATA SOURCES: MCP servers connected to this project for your role."]
    if inline:
        lines.append(
            " Mounted as tools: "
            + ", ".join(f"{n} ({ref.format(server=n, tool='*')})" for n in inline)
            + ". If a listed source's tools are absent from your toolset, report that "
            "plainly instead of guessing at causes."
        )
    if bulk:
        lines.append(
            " Bulk-only (deliberately NOT mounted as tools, because their results "
            "belong in files rather than in your context): "
            + ", ".join(bulk)
            + ". Reach them with data_fetch — call data_fetch(server=\"<name>\") with no "
            "tool to see what each one offers."
        )
    lines.append(
        "\n\nDATA HANDLING: every result a mounted source's tool returns is copied into "
        "your context and paid for by the token, on that turn and on every turn after "
        "it. Use those tools only for small lookups you intend to read in full. For "
        "anything that could exceed a screenful — exports, row-level data, logs, "
        "listings — call data_fetch(server, tool, arguments) instead: it makes the same "
        "call but writes the complete result to a file in your workspace and returns "
        "only the path, size and a short preview. Then process the FILE with code "
        "(python3 has pandas and duckdb; use Bash if you have it, otherwise Read and "
        "Grep slices of the file) and report only the aggregate. Three habits follow "
        "from this: prefer a source tool's own filter, date-range or pagination "
        "arguments over fetching everything; check your workspace before fetching "
        "anything twice, because it persists across runs and yesterday's file is free; "
        "and leave the script that produced an answer behind under scripts/, so the "
        "next run of the same analysis re-executes it instead of re-deriving it. "
        "Counting, summing, sorting and joining are things code does exactly and "
        "cheaply — doing them by reading rows costs tokens and gets answers wrong."
    )
    return "".join(lines)


def _merge_project_data_sources(session: Session, project: Project, role_version,
                                job: RunJob) -> list[str]:
    """Data sources are project-owned (config.mcp_servers), mounted for the
    roles they are assigned to — roles never declare their own servers. The
    mapping lives on the project and is applied at lease time, which is why
    connecting a source needs no role-library edit and no team repin. Returns
    the merged specs (name + access), which the prompt paragraph describes."""
    # Pinned versions minted before data sources became project-owned may still
    # freeze a legacy mcp_servers key; it is dead weight, never mounted.
    job.tool_permissions.pop("mcp_servers", None)
    specs: list[dict] = []
    if role_version is not None:
        from yaaf.core.models import Role

        role = session.get(Role, role_version.role_id)
        for source in (project.integrations or {}).get("mcp_servers", []):
            if role is None or role.slug not in source.get("roles", []):
                continue
            if source.get("auth") == "oauth":
                # An OAuth access token expires; renew it (into the same
                # mcp-<name>-token secret row) before this run inherits it, so
                # a long-lived source never mounts with yesterday's token. A
                # failed renewal keeps the stale token mounted and marks the
                # source needs_reauth — the health page says why, the agent
                # sees a named auth error instead of a silently absent source.
                from yaaf.server.mcp_oauth import ensure_fresh_token

                ensure_fresh_token(session, project, source)
            spec = {"name": source["name"],
                    "url_env": _env_var_name(source["url_secret"]),
                    # bulk sources are reachable only through data_fetch (which
                    # writes to a workspace file); the sandbox never mounts them
                    # as tools, so their rows cannot enter a model's context.
                    "access": source.get("access", "inline")}
            if source.get("tools") is not None:
                # The allowlist travels with the source, so the sandbox refuses
                # an ungranted tool without asking this process anything.
                spec["tools"] = list(source["tools"])
            if source.get("bearer_secret"):
                spec["bearer_env"] = _env_var_name(source["bearer_secret"])
            specs.append(spec)
        if specs:
            # New dict, never in-place: job.tool_permissions shallow-copies the
            # role version's dict and must not mutate its nested lists.
            job.tool_permissions = {**job.tool_permissions, "mcp_servers": specs}
    return specs


def _prepare_workspace(session: Session, project: Project) -> str:
    """The job's workspace path. GitHub-backed projects get their repo cloned
    on first touch; after that (or on any failure) the workspace is exactly
    the plain local dir it always was — agents own the working tree, and a
    workspace must never hard-block a run."""
    workspace = Path(settings().workspaces_root) / project.slug
    if project.github_repo and not (workspace / ".git").exists():
        _clone_workspace(session, project.github_repo, workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    return str(workspace)


def _clone_workspace(session: Session, full_name: str, workspace: Path) -> None:
    """Clone with the platform "github" token, then scrub the credential from
    the remote so nothing under .git ever holds it — agents push with their
    own role-scoped secret. Failures log and degrade to the plain dir; log
    lines and exceptions are kept token-free (a TimeoutExpired repr would
    happily print the whole authenticated URL)."""
    import subprocess

    from yaaf.server import github

    token = github.platform_token(session)
    if token is None:
        log.warning("project repo %s configured but the platform 'github' secret is "
                    "missing; using a plain local dir", full_name)
        return
    workspace.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", "clone", github.clone_url(full_name, token), str(workspace)],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        _scrub_remote(workspace, full_name)  # a killed clone may have written .git
        log.warning("clone of %s failed (%s); using a plain local dir",
                    full_name, type(exc).__name__)
        return
    if proc.returncode != 0:
        _scrub_remote(workspace, full_name)
        log.warning("clone of %s failed; using a plain local dir: %s", full_name,
                    (proc.stderr or "").replace(token, "<redacted>").strip()[:500])
        return
    _scrub_remote(workspace, full_name)
    log.info("cloned %s into %s", full_name, workspace)


def _scrub_remote(workspace: Path, full_name: str) -> None:
    """Point origin back at the credential-free URL. If even that fails, the
    .git dir is removed outright — leaving the token behind is the one
    unacceptable outcome."""
    import shutil
    import subprocess

    from yaaf.server import github

    if not (workspace / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "-C", str(workspace), "remote", "set-url", "origin",
             github.public_url(full_name)],
            capture_output=True, timeout=30, check=True,
        )
    except Exception:
        shutil.rmtree(workspace / ".git", ignore_errors=True)
        log.warning("could not scrub the clone credential for %s; dropped .git", full_name)


def _env_var_name(secret_name: str) -> str:
    from yaaf.core.naming import env_var_name

    return env_var_name(secret_name)


def _file_cred_carrier(env_name: str) -> str:
    from yaaf.core.naming import file_cred_carrier

    return file_cred_carrier(env_name)


def _role_secret_env(session: Session, project: Project, role_version) -> dict:
    """Secrets whose role_scopes name this agent's role, decrypted and named as
    env vars. The grant lives on the SECRET (chairman-editable, effective on
    the next run) — never on the role version, which teams pin at creation.
    Empty role_scopes means injected nowhere: billing credentials and
    not-yet-granted secrets stay out of every sandbox."""
    if role_version is None:
        return {}
    from sqlalchemy import or_

    from yaaf.core.crypto import decrypt
    from yaaf.core.models import Role

    role = session.get(Role, role_version.role_id)
    if role is None:
        return {}
    candidates = session.execute(
        select(Secret)
        .where(or_(Secret.project_id == project.id, Secret.project_id.is_(None)))
        .order_by(Secret.project_id.is_(None))  # project-scoped wins over platform
    ).scalars()
    env: dict = {}
    for secret in candidates:
        if secret.kind == "billing":
            continue  # belt and suspenders: the API refuses grants on billing
            # secrets, but even a hand-edited DB row must not leak the model
            # credential into a role's env.
        if not secret.role_scopes or role.slug not in secret.role_scopes:
            continue
        var = _env_var_name(secret.name)
        if var in env or _file_cred_carrier(var) in env:
            continue  # project-scoped already claimed this name
        try:
            value = decrypt(secret.ciphertext)
        except Exception:
            log.warning("could not decrypt secret %s for role scope", secret.id)
            continue
        if secret.filename:
            # The bytes ride under the carrier and never under `var` itself:
            # the entrypoint writes the file, points `var` at it and drops the
            # carrier, so what the model can read is a path. Doing it here
            # instead would mean handing an agent a private key and a note
            # asking it to save the key somewhere — inside a git clone.
            env[_file_cred_carrier(var)] = json.dumps(
                {"filename": secret.filename, "b64": value})
        else:
            env[var] = value
    return env


def _billing_env(session: Session, project: Project, provider: str = DEFAULT_PROVIDER) -> dict:
    """The model credential a seat on `provider` pays with, as the one variable
    its kind is injected under — the billing credential where it pays that
    provider, else the project's credential for it (R5 refuses a seat with
    neither, at every door and at lease)."""
    from yaaf.core import project_credentials

    secret = project_credentials.credential_for(session, project, provider)
    if secret is None:
        return {}
    from yaaf.core.crypto import decrypt

    if secret.filename:
        # A model credential is a string by definition; only a hand-edited row
        # could get here. Injecting base64 as ANTHROPIC_API_KEY would fail every
        # call with "Not logged in", which describes nothing.
        log.warning("billing secret %s is stored as a file; refusing to inject it", secret.id)
        return {}
    if secret.provider not in PROVIDERS:
        # A provider this install does not have: a row a newer release wrote,
        # then rolled back. Refused the same way as a file — this project's
        # runs fail unauthenticated and pause it on the third. Raising instead
        # would abort the lease transaction, and with it every other project's
        # runs on the box.
        log.warning("billing secret %s pays provider %r, which this install does not "
                    "have; refusing to inject it", secret.id, secret.provider)
        return {}
    mode = project.billing_mode if secret.id == project.billing_secret_id else secret.llm_mode
    return credential_env(secret.provider, mode, decrypt(secret.ciphertext))


def _provider_spec(session: Session, project: Project, provider: str) -> dict:
    """What a harness that calls the provider's API itself needs to know about
    it, for a sandbox that cannot import the registry: where it is, which
    variable holds the key, whether it states its costs and searches, the
    words its refusals use for a dead key. The
    address is the credential's where the provider's is (a custom server)."""
    from yaaf.core import project_credentials

    seller = PROVIDERS.get(provider)
    if seller is None:
        return {}
    secret = project_credentials.credential_for(session, project, provider)
    mode = (project.billing_mode if secret is not None and secret.id == project.billing_secret_id
            else secret.llm_mode if secret is not None else None)
    base_url = (secret.base_url if seller.credential_base_url and secret is not None
                else seller.base_url)
    return {"name": seller.name, "base_url": base_url, "key_env": seller.kind(mode).env_var,
            "reports_cost": seller.reports_cost, "search": seller.search,
            "failure_markers": list(seller.failure_markers)}


# ── execution ───────────────────────────────────────────────────────────────────


def execute_job(job: RunJob, backend=None) -> RunResult:
    stop_heartbeat = threading.Event()
    revoked = threading.Event()
    beat = threading.Thread(
        target=_heartbeat, args=(job.run_id, stop_heartbeat, revoked), daemon=True
    )
    beat.start()
    try:
        # Both check kinds are COMMANDS the platform runs and reads the exit
        # code of — never model runs. Sending one to the model backend launches
        # a sandbox with no token, no prompt and no billing credential, which
        # dies as "Not logged in" and returns no exit code at all: the
        # requirement then fails a test that never ran, forever.
        if job.kind in (RunKind.SUCCESS_CHECK, RunKind.REQUIREMENT_CHECK):
            result = _run_check_command(job)
        else:
            backend = backend or _backend_for(job)
            # The revoked event reaches the sandbox layer so a kill switch,
            # pause, or budget trip stops the container, not just its token.
            result = backend.execute(job, stop_event=revoked)
    except Exception as exc:
        log.exception("job %s crashed in backend", job.run_id)
        result = RunResult(ok=False, summary=f"backend crashed: {exc!r}")
    finally:
        stop_heartbeat.set()
    finalize(job, result, was_revoked=revoked.is_set())
    return result


def _backend_for(job: RunJob):
    """Each seat runs on its own harness, under the project's override — the
    same rule the lease pinned on the run (yaaf.runtimes.seats)."""
    with db_session() as session:
        project = session.get(Project, job.project_id)
        runtime = (project.integrations or {}).get("runtime")
    return get_backend(effective_harness(job.harness, runtime))


def _heartbeat(run_id: str, stop: threading.Event, revoked: threading.Event) -> None:
    interval = settings().lease_heartbeat_s
    while not stop.wait(interval):
        try:
            with db_session() as session:
                run = session.get(Run, run_id)
                if run is None:
                    return
                run.heartbeat_at = utcnow()
                if run.token_revoked:
                    revoked.set()
        except Exception:
            log.warning("heartbeat failed for %s", run_id, exc_info=True)


def finalize(job: RunJob, result: RunResult, was_revoked: bool = False) -> None:
    with db_session() as session:
        run = session.get(Run, job.run_id)
        if run is None or run.status not in (RunStatus.RUNNING,):
            return  # reaped or killed while we worked; the janitor won
        if was_revoked or run.token_revoked:
            run.status = (
                RunStatus.TERMINATED_REFUSALS if run.refusal_count else RunStatus.KILLED
            )
        else:
            run.status = RunStatus.DONE if result.ok else RunStatus.FAILED
        run.finished_at = utcnow()
        if run.status != RunStatus.DONE:
            policy.charge_failure(session, run)
        run.tokens_in = result.tokens_in
        run.tokens_out = result.tokens_out
        run.cache_creation_tokens = result.cache_creation_tokens
        run.cache_read_tokens = result.cache_read_tokens
        # Tokens always; dollars only when dollars move. Subscription runs are
        # a rate limit, not a bill — booking the SDK's list-price figure would
        # distort every budget decision (the budget gate for subscription
        # projects runs on monthly_token_cap instead).
        project = session.get(Project, job.project_id)
        subscription = project is not None and project.billing_mode == "subscription"
        run.cost_usd = None if subscription else result.cost_usd
        run.priced_usd = None if subscription else result.priced_usd
        run.trace_path = _stash_trace(job) or result.trace_path
        run.context_path = _stash_context(job)
        _clear_staged_inputs(job)
        run.summary = (result.summary or "")[:4000]
        run.error_kind = result.error_kind

        # Outcome metering. A wake cannot be known to be empty until it has
        # happened, so the refund is retroactive: the run is logged, billed to
        # the ledger and shown on the Runs page exactly as before, but it stops
        # counting against the work allowance. The project was not slowed by a
        # run that changed nothing, and the allowance should not pretend it was.
        #
        # Only ever a refund, never a charge: a run that DID produce keeps the
        # `metered=True` it was born with. And only for the work lane — an
        # inbox run producing nothing is the ordinary case, and refunding those
        # would leave that lane with no ceiling at all.
        if run.kind not in NON_WORK_RUN_KINDS and not run.produced:
            run.metered = False

        # The run was told what is left on the plan behind its own credential.
        # Filing it here is the whole meter: no request to Anthropic, and it
        # refreshes exactly as often as the credential is actually used.
        if result.plan and project is not None and project.billing_secret_id:
            from yaaf.core import plan_usage
            from yaaf.core.crypto import decrypt

            billing = session.get(Secret, project.billing_secret_id)
            if billing is not None:
                try:
                    plan_usage.record(session, decrypt(billing.ciphertext), result.plan)
                except Exception:
                    # A meter is a nicety; a run result is not. Never let the
                    # reading be the reason a finished run fails to book.
                    log.exception("could not file the plan reading for run %s", run.id)

        if (result.tokens_in or result.tokens_out or result.cache_creation_tokens
                or result.cache_read_tokens or run.cost_usd):
            session.add(
                LedgerEntry(
                    id=ids.new_id(ids.LEDGER),
                    project_id=job.project_id,
                    kind=LedgerKind.MODEL_USAGE,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    cache_creation_tokens=result.cache_creation_tokens,
                    cache_read_tokens=result.cache_read_tokens,
                    usd=run.cost_usd,
                    run_id=run.id,
                    note=f"{run.kind} {run.status}"
                    + (" (subscription: tokens only)" if subscription else ""),
                )
            )

        if job.kind == RunKind.SUCCESS_CHECK:
            _finalize_success_check(session, job, result)
        elif job.kind == RunKind.REQUIREMENT_CHECK:
            _finalize_requirement_check(session, job, result)
        elif run.status == RunStatus.FAILED:
            if project is not None and _looks_like_credential_failure(run):
                _flag_credential_failure(session, project, run)
            _retry_failed_run(session, run)


CREDENTIAL_FAILURE_MARKERS = failure_markers()


def _looks_like_credential_failure(run: Run) -> bool:
    """A dead credential, as the harness classified it — or, for a run whose
    harness said nothing, as its summary reads. The markers stay as the second
    reading because a credential can also die in prose the model was handed
    back rather than in an exception the harness caught."""
    if getattr(run, "error_kind", None) == "auth":
        return True
    lowered = (run.summary or "").lower()
    return any(marker in lowered for marker in CREDENTIAL_FAILURE_MARKERS)


def _flag_credential_failure(session: Session, project: Project, run: Run) -> None:
    """A dead billing credential fails every run it touches, and the scheduler
    would happily wake agents into it forever. So: audit every occurrence,
    chase the chairman at most once a day — and after three consecutive
    credential-failed runs, pause the project outright, exactly like budget
    exhaustion. A notification is not an enforcement mechanism; the pause is."""
    from datetime import timedelta

    from yaaf.core.models import AuditLog
    from yaaf.server.notify import Attention, chairman_notify

    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="runner",
        action="billing.credential_failure", entity="run", entity_id=run.id,
        detail={"summary": (run.summary or "")[:300]},
    ))
    # Counted per provider: a project paying two providers has two credentials,
    # and one dying while the other's seats succeed must still be caught.
    on_provider = [Run.provider == run.provider] if run.provider else []
    recent = session.execute(
        select(Run)
        .where(Run.project_id == project.id, Run.kind.in_(MODEL_RUN_KINDS),
               Run.finished_at.is_not(None), *on_provider)
        .order_by(Run.finished_at.desc())
        .limit(3)
    ).scalars().all()
    dead = len(recent) == 3 and all(
        r.status == RunStatus.FAILED and _looks_like_credential_failure(r)
        for r in recent
    )
    if dead and project.status == ProjectStatus.RUNNING:
        project.status = ProjectStatus.PAUSED
        which = (f"{PROVIDERS[run.provider].label} credential"
                 if run.provider in PROVIDERS and run.provider != DEFAULT_PROVIDER
                 else "billing credential")
        project.status_reason = (f"{which} invalid: 3 consecutive runs failed "
                                 "to authenticate")
        for queued in session.execute(
            select(RunQueue).where(RunQueue.project_id == project.id)
        ).scalars():
            session.delete(queued)
        chairman_notify(
            session, project, "project auto-paused: billing credential invalid",
            "Three consecutive runs failed to authenticate ('Not logged in' / invalid "
            "key). No run can succeed until the credential is fixed. Reconnect billing "
            "in Settings (the 'test' button verifies it), then resume the project.",
            attention=Attention.ALARM,
        )
        log.warning("project %s auto-paused: credential failures", project.slug)
        return
    from sqlalchemy import func

    day_ago = utcnow() - timedelta(hours=24)
    already = session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == project.id,
            AuditLog.action == "billing.credential_notify",
            AuditLog.at >= day_ago,
        )
    ).scalar_one()
    if not already:
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=project.id, actor="runner",
            action="billing.credential_notify", entity="project", entity_id=project.id,
            detail={},
        ))
        chairman_notify(
            session, project, "a run failed to authenticate",
            "A run failed with a credential error. If this repeats twice more, the "
            "project will pause itself. Check Settings → billing (the 'test' button "
            "verifies the credential without burning a run).",
            attention=Attention.FYI,
        )


def _stash_trace(job: RunJob) -> str | None:
    """Copy the run's trace out of the (ephemeral, runner-only) workspace into
    the artifact store, where the API can serve it. Best-effort: a missing
    trace never fails a finalize."""
    from yaaf.core.artifacts import store

    try:
        src = Path(job.workspace_dir) / ".yaaf" / f"trace-{job.run_id}.jsonl"
        if not src.exists():
            return None
        return store().put_bytes(f"traces/{job.run_id}.jsonl", src.read_bytes())
    except Exception:
        log.warning("could not stash trace for %s", job.run_id, exc_info=True)
        return None


def _clear_staged_inputs(job: RunJob) -> None:
    """Drop this run's staged prompt and pack once they are stashed. Run-keyed
    names mean they no longer overwrite each other — so without this they
    accumulate one pair per run in a workspace that lives as long as the
    project. Best-effort: never fails a finalize."""
    staging = Path(job.workspace_dir) / ".yaaf"
    for name in (f"system_prompt-{job.run_id}.txt", f"context-{job.run_id}.json"):
        try:
            (staging / name).unlink(missing_ok=True)
        except Exception:
            log.warning("could not clear staged input %s for %s", name, job.run_id)


def _stash_context(job: RunJob) -> str | None:
    """Stash what the run actually sent to the model — the staged system prompt
    and context pack — so the chairman can audit a run's inputs, not just its
    outputs. Best-effort, like _stash_trace: never fails a finalize."""
    import json

    try:
        staging = Path(job.workspace_dir) / ".yaaf"
        # Run-keyed, and deliberately with NO fallback to the old shared names.
        # A run that staged nothing must stash nothing: success checks never
        # stage a pack at all, so any fallback hands them whichever file was
        # lying in a workspace every agent on the project shares — which is how
        # a check run came to be recorded against another agent's context from
        # the day before. An empty audit record is honest; a confident wrong
        # one is worse than none.
        prompt_file = staging / f"system_prompt-{job.run_id}.txt"
        context_file = staging / f"context-{job.run_id}.json"
        system_prompt = prompt_file.read_text() if prompt_file.exists() else None
        context_pack = (json.loads(context_file.read_text())
                        if context_file.exists() else None)
        if system_prompt is None and context_pack is None:
            return None
        return stash_context_doc(job.run_id, system_prompt, context_pack)
    except Exception:
        log.warning("could not stash context for %s", job.run_id, exc_info=True)
        return None


def stash_context_doc(run_id: str, system_prompt: str | None,
                      context_pack: dict | None) -> str | None:
    """Write the {"system_prompt", "context_pack"} document to the artifact
    store as ``contexts/{run_id}.json``. Shared with the chat supervisor, whose
    context is not a staged file. Returns the store uri, or None on failure."""
    import json

    from yaaf.core.artifacts import store

    try:
        doc = json.dumps({"system_prompt": system_prompt, "context_pack": context_pack})
        return store().put_bytes(f"contexts/{run_id}.json", doc.encode())
    except Exception:
        log.warning("could not stash context for %s", run_id, exc_info=True)
        return None


MAX_RUN_ATTEMPTS = 3


def _retry_failed_run(session: Session, run: Run) -> None:
    """A failed run consumed its trigger (notices marked read, suggestions
    marked read, message cursors advanced) without a model ever acting on it.
    Give them back and requeue the wake with backoff; the retry's context pack
    re-includes recent messages (see contextpack) so nothing said gets silently
    eaten."""
    from datetime import timedelta

    from yaaf.core.models import Notice, Suggestion

    for notice in session.execute(
        select(Notice).where(Notice.consumed_by_run_id == run.id)
    ).scalars():
        notice.consumed_at = None
        notice.consumed_by_run_id = None

    # Suggestions are consumed by pack construction the same way notices are,
    # and are an employee's ONLY authority over the backlog. Losing one to a
    # crash silently deletes the single thing a non-CEO agent may say about
    # what should be worked on.
    for suggestion in session.execute(
        select(Suggestion).where(Suggestion.consumed_by_run_id == run.id)
    ).scalars():
        suggestion.consumed_at = None
        suggestion.consumed_by_run_id = None

    if run.attempt >= MAX_RUN_ATTEMPTS:
        log.warning("run %s failed at attempt %s; giving up", run.id, run.attempt)
        return
    session.add(
        RunQueue(
            id=ids.new_id(ids.RUN),
            project_id=run.project_id,
            agent_id=run.agent_id,
            kind=run.kind,
            trigger=run.trigger,
            task_id=run.task_id,
            attempt=run.attempt + 1,
            prior_run_id=run.id,
            not_before=utcnow() + timedelta(seconds=60 * run.attempt),
        )
    )


# How many prior attempts an agent is shown, and how much of each. Enough to
# see a pattern in what it has already tried; small enough that a task row
# stays a task row.
CHECK_HISTORY_KEEP = 5
CHECK_HISTORY_EVIDENCE_CHARS = 300
CHECK_HISTORY_OUTPUT_CHARS = 500


def _record_check_attempt(task: Task, output: str) -> None:
    """Append this failed attempt to the task's rolling history.

    The *evidence* half is the point: it is the claimant's own account of what
    it changed, and it is the only thing that tells a later run "you already
    tried that". Without it an agent sees one failure at a time and can cycle
    between two rejected fixes forever."""
    history = list(task.check_history or [])
    history.append({
        "at": utcnow().isoformat(),
        "evidence": (task.completion_evidence or "")[:CHECK_HISTORY_EVIDENCE_CHARS],
        "output": output[:CHECK_HISTORY_OUTPUT_CHARS],
    })
    task.check_history = history[-CHECK_HISTORY_KEEP:]


def _finalize_requirement_check(session: Session, job: RunJob, result: RunResult) -> None:
    """The only place `met` is ever written by a command test.

    A requirement is not done because an agent says so and not because its
    tasks are done — five deliverables in the audited week were marked DONE
    while failing their own stated success test, each with a confident status
    report attached. It is done because the platform ran the acceptance test
    and read the exit code itself.

    A failure does not block or nag: it reopens the requirement and adds to its
    failure load, which is what lets a review say the rate fell because one
    requirement ate fifty runs rather than that the project got slower.
    """
    requirement = session.get(Requirement, job.requirement_id)
    if requirement is None or requirement.status != RequirementStatus.CHECKING:
        return
    output = (result.check_output or "")[:4000]
    requirement.check_output = output
    from_status = requirement.status

    if result.check_exit_code == 0:
        requirement.status = RequirementStatus.MET
        requirement.met_at = utcnow()
        requirement.met_by_run_id = job.run_id
        policy.record_requirement_event(session, requirement, RequirementStatus.MET,
                                        "checker", note="acceptance test passed",
                                        from_status=from_status)
        _notice_ceo_requirement(
            session, requirement,
            f"{requirement.key} is MET. The platform ran its acceptance test and it "
            f"passed (exit 0). {requirement.statement[:300]}",
        )
        return

    requirement.status = RequirementStatus.IN_PROGRESS
    requirement.failure_load += 1
    policy.record_requirement_event(
        session, requirement, RequirementStatus.IN_PROGRESS, "checker",
        note=f"acceptance test FAILED (exit {result.check_exit_code})",
        from_status=from_status,
    )
    try:
        cap = effective(
            session, requirement.project_id
        ).requirement_check_failures_before_escalation
    except LookupError:  # no constitution: never worse than the old behaviour
        cap = 0

    if cap and requirement.failure_load > cap:
        # Escalated once already, and nothing has been decided since. Another
        # copy of the same traceback is not new information — one project piled
        # up 305 unconsumed notices, 116k characters of them, all waiting to be
        # delivered into a single CEO run.
        return

    if cap and requirement.failure_load == cap:
        _notice_ceo_requirement(
            session, requirement,
            f"{requirement.key} has now failed its acceptance test {cap} times, each one "
            f"after work closed under it. Stop sending work at it and read the test: "
            f"either the plan is not building what the test asserts, or the test asserts "
            f"something the plan was never going to build. Deciding which is yours.\n\n"
            f"The requirement: {requirement.statement[:300]}\n\n"
            f"What the test printed:\n{output[:1200]}",
        )
        return

    _notice_ceo_requirement(
        session, requirement,
        f"{requirement.key} is NOT met: its acceptance test failed (exit "
        f"{result.check_exit_code}) even though every task under it closed. That gap is "
        f"the thing worth looking at — the work was reported done and the requirement is "
        f"still false.\n\nWhat the test printed:\n{output[:1200]}",
    )


def _notice_ceo_requirement(session: Session, requirement, body: str) -> None:
    ceo = policy.ceo_of(session, requirement.project_id)
    if ceo is None:
        return
    session.add(Notice(
        id=ids.new_id(ids.NOTICE),
        project_id=requirement.project_id,
        agent_id=ceo.id,
        kind=NoticeKind.GOAL_CHANGED,
        body=body,
    ))


def _finalize_success_check(session: Session, job: RunJob, result: RunResult) -> None:
    task = session.get(Task, job.task_id)
    if task is None or task.status != TaskStatus.CHECKING:
        return
    output = (result.check_output or "")[:4000]
    task.check_output = output
    if result.check_exit_code == 0:
        from_status = task.status
        task.status = TaskStatus.DONE
        task.check_failures = 0
        task.check_history = None
        policy.record_task_event(session, task, TaskStatus.DONE, "checker",
                                 note="success test passed", from_status=from_status)
        return

    task.check_failures = (task.check_failures or 0) + 1
    _record_check_attempt(task, output)
    try:
        cap = effective(session, task.project_id).task_check_failures_before_block
    except LookupError:  # no constitution: never worse than the old behaviour
        cap = 0
    from_status = task.status

    if cap and task.check_failures >= cap:
        _block_on_repeated_check_failure(session, task, result, output, from_status, cap)
        return

    task.status = TaskStatus.CLAIMED  # back to the claimant with the output
    policy.record_task_event(
        session, task, TaskStatus.CLAIMED, "checker",
        note=f"success test FAILED (exit {result.check_exit_code}), "
             f"attempt {task.check_failures}",
        from_status=from_status,
    )
    if task.claimed_by_agent_id:
        remaining = cap - task.check_failures if cap else None
        session.add(
            Notice(
                id=ids.new_id(ids.NOTICE),
                project_id=task.project_id,
                agent_id=task.claimed_by_agent_id,
                kind=NoticeKind.TASK_CHECK_FAILED,
                body=(
                    f"ATTEMPT {task.check_failures}"
                    + (f" OF {cap}" if cap else "")
                    + f": the platform ran your task's success test and it FAILED "
                      f"(exit {result.check_exit_code}). The task is claimed by you again."
                    + (f" {remaining} attempt(s) left before it blocks and goes back to "
                       "whoever planned it." if remaining else "")
                    + " Read `task.previous_attempts` in your context before changing "
                      "anything: if you have already tried a fix and it failed, trying it "
                      "again will fail again. If the same rule keeps rejecting materially "
                      "different submissions, the check itself is the likely fault — block "
                      "the task and say so rather than guessing another edit."
                    + f"\nOutput:\n{output[:1500]}"
                ),
            )
        )


def _block_on_repeated_check_failure(
    session: Session, task: Task, result: RunResult, output: str,
    from_status: str, cap: int,
) -> None:
    """The loop breaker. A task that cannot pass its own success test is not a
    task the claimant can finish by trying harder — it is a question for
    whoever planned it, and on a repeat, for the chairman."""
    from yaaf.server.notify import Attention, chairman_notify

    reason = (
        f"Success test failed {task.check_failures} times running (exit "
        f"{result.check_exit_code}); the platform stopped re-waking the claimant. "
        f"Last output:\n{output[:1500]}"
    )
    task.status = TaskStatus.BLOCKED
    task.blocked_reason = reason
    claimant_id = task.claimed_by_agent_id
    task.claimed_by_agent_id = None
    task.claim_run_id = None
    policy.record_task_event(
        session, task, TaskStatus.BLOCKED, "checker",
        note=f"blocked after {task.check_failures} failed checks (cap {cap})",
        from_status=from_status,
    )

    # Has this exact check killed a task before? If so the checker is the
    # suspect, and the planner is not the right person to hear it twice: its
    # only lever is kill-and-re-issue, which mints a task with a fresh counter.
    prior = policy.tasks_dead_on_check(session, task.project_id, task.success_test,
                                       exclude_task_id=task.id)
    planner = policy.planner_of(session, task)
    # A subtask's planner is its parent's claimant, and that agent can be gone —
    # or be the very claimant we are blocking. Either way the notice used to go
    # nowhere at all: the task sat BLOCKED and no one in the project was told.
    # The CEO is the correct fallback; it owns the board.
    if planner is None or planner.id == claimant_id:
        planner = policy.ceo_of(session, task.project_id)
    if planner is not None and planner.id != claimant_id:
        session.add(
            Notice(
                id=ids.new_id(ids.NOTICE),
                project_id=task.project_id,
                agent_id=planner.id,
                kind=NoticeKind.TASK_BLOCKED,
                body=(
                    f"Task '{task.title}' ({task.id}) was BLOCKED by the platform: its "
                    f"success test failed {task.check_failures} times running. Nobody is "
                    "working it now. Re-plan it: change it, kill it, or fix the check. "
                    + (f"NOTE: {len(prior)} earlier task(s) also died on this exact "
                       "success test and none ever passed it — re-issuing it unchanged "
                       "will produce this same message again. The check is the suspect, "
                       "not the deliverable. The chairman has been told.\n"
                       if prior else "")
                    + f"\n{reason}"
                ),
            )
        )
    project = session.get(Project, task.project_id)
    if prior and project is not None:
        chairman_notify(
            session, project,
            "a success test has now killed two tasks",
            f"Task {task.id} blocked after {task.check_failures} failed checks, and "
            f"{len(prior)} earlier task(s) died on the same success test without ever "
            f"passing it ({', '.join(t.id for t in prior[:5])}). Further tasks carrying "
            f"this check are refused until you intervene.\n\n"
            f"Check: {(task.success_test or {}).get('command', '')[:500]}\n\n{reason}",
            attention=Attention.ALARM,
        )


def _run_check_command(job: RunJob) -> RunResult:
    """Execute a command-type test — a task's success test or a requirement's
    acceptance test. The agent never self-reports either; the platform runs the
    command and reads the exit code."""
    from yaaf.runner.sandbox import run_check_command

    label = "success check" if job.kind == RunKind.SUCCESS_CHECK else "acceptance test"
    command = (job.check_command or "").strip()
    if not command:
        # An empty command run by a shell exits 0, and exit 0 is what marks a
        # requirement MET. Nothing may pass by being blank — that is the exact
        # unearned "done" the checker exists to prevent.
        return RunResult(
            ok=False,
            summary=f"{label} has no command to run",
            check_exit_code=None,
            check_output=f"the {label} is a command test but its command is empty",
        )

    exit_code, output = run_check_command(
        command=command,
        workspace_dir=job.workspace_dir,
        timeout_s=job.timeout_s,
    )
    return RunResult(
        ok=True,
        summary=f"{label} exit={exit_code}",
        check_exit_code=exit_code,
        check_output=output,
    )
