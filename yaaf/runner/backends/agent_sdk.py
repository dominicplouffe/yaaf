"""The real backend: a harness inside a Docker sandbox.

The host side (this file) stages inputs into the workspace, launches the
sandbox container, and reads the result back. The model runs *inside* the
container: its only credentials are the per-run yaaf token and the project's
billing credential, its only network is the open network, and its only
platform surface is the agent HTTP API.

One backend for every harness that runs in the container. What differs between
them is only the entry script — ``sandbox_entry.py`` drives the Agent SDK,
``claude_cli_entry.py`` the Claude CLI, ``codex_cli_entry.py`` the Codex CLI,
``gemini_cli_entry.py`` the Gemini CLI, ``opencode_cli_entry.py`` OpenCode,
``loop_entry.py`` yaaf's own loop on whichever wire the provider speaks — and
every one of them reads the same staged files and writes
the same result.json (docs/RUNTIMES.md).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from yaaf.runner.types import RunJob, RunResult

log = logging.getLogger("yaaf.agent_sdk")


def _skill_slug(name: str) -> str:
    """Mirror of sandbox_entry._skill_slug — the staged directory name must be
    exactly what the allowlist inside the sandbox will pass to the SDK."""
    return "".join(
        ch for ch in name.lower().strip().replace(" ", "-")
        if ch.isalnum() or ch == "-"
    )[:64]


def stage_skills(workspace: Path, specs: list) -> None:
    """Write the chairman's role-defined skills where the CLI discovers them.

    The platform OWNS .claude/skills: wiped and rebuilt every run, so the
    staged set is exactly the current role definition. An agent-written skill
    file would be dark anyway — the sandbox passes an explicit allowlist to
    the SDK — but a clean directory keeps the on-disk listing honest too.
    """
    skills_root = workspace / ".claude" / "skills"
    if skills_root.exists():
        shutil.rmtree(skills_root, ignore_errors=True)
    for spec in specs or []:
        slug = _skill_slug(str(spec.get("name", "")))
        body = str(spec.get("body", "")).strip()
        if not slug or not body:
            continue
        description = str(spec.get("description", "")).strip() or slug
        skill_dir = skills_root / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: {description}\n---\n\n{body}\n"
        )
    if skills_root.exists():
        # The sandbox runs unprivileged; the runner often does not.
        for path in [workspace / ".claude", skills_root, *skills_root.rglob("*")]:
            path.chmod(0o777 if path.is_dir() else 0o666)


class SandboxBackend:
    def __init__(self, entrypoint: str = "sandbox_entry.py"):
        self.entrypoint = entrypoint

    def execute(self, job: RunJob, stop_event=None) -> RunResult:
        from yaaf.runner.sandbox import run_agent_container

        workspace = Path(job.workspace_dir)
        staging = workspace / ".yaaf"
        staging.mkdir(parents=True, exist_ok=True)
        # The sandbox runs as an unprivileged user; the runner (often root in a
        # container) stages these dirs, so open them up or nothing comes back.
        workspace.chmod(0o777)
        staging.chmod(0o777)
        # Keyed by run, like the trace and result files beside them. The
        # workspace is per-*project* and shared by every agent in it, so fixed
        # names here are a race: three runs starting in the same second wrote
        # over each other's context and all three died reading half a document.
        # A crash is the lucky outcome — with kinder timing a run reads another
        # agent's pack, complete with its task and its messages, and says
        # nothing.
        (staging / f"system_prompt-{job.run_id}.txt").write_text(job.system_prompt)
        (staging / f"context-{job.run_id}.json").write_text(
            json.dumps(job.context_pack, default=str))
        result_path = staging / f"result-{job.run_id}.json"
        if result_path.exists():
            result_path.unlink()
        stage_skills(workspace, (job.tool_permissions or {}).get("skills") or [])

        env = {
            "YAAF_RUN_ID": job.run_id,
            "YAAF_API_BASE_URL": job.api_base_url,
            "YAAF_API_TOKEN": job.api_token,
            "YAAF_MODEL": job.model,
            "YAAF_EFFORT": job.effort,
            "YAAF_MAX_USD": str(job.max_usd or 0),
            "YAAF_TASK_BUDGET": str(job.task_budget_tokens or 0),
            "YAAF_PRICE": json.dumps(job.price) if job.price else "",
            "YAAF_MODEL_FLAGS": json.dumps(job.model_flags or {}),
            "YAAF_PROVIDER": json.dumps(job.provider_spec or {}),
            "YAAF_TOOL_PERMISSIONS": json.dumps(job.tool_permissions),
            "YAAF_AGENT_KIND": job.agent_kind,
            **job.billing_env,
        }
        exit_code, logs = run_agent_container(
            workspace_dir=str(workspace), env=env, timeout_s=job.timeout_s,
            stop_event=stop_event, entrypoint=self.entrypoint,
        )

        if result_path.exists():
            try:
                data = json.loads(result_path.read_text())
                summary = str(data.get("summary", ""))[:4000]
                if data.get("partial"):
                    # The entry script never finished, but it checkpointed usage
                    # after every message — the kill is metered, not free.
                    summary = (f"[killed mid-run, exit {exit_code}; usage recovered from "
                               f"checkpoint]\n{summary}")
                return RunResult(
                    ok=bool(data.get("ok")) and not data.get("partial"),
                    summary=summary,
                    tokens_in=int(data.get("tokens_in", 0)),
                    tokens_out=int(data.get("tokens_out", 0)),
                    cache_creation_tokens=int(data.get("cache_creation_tokens", 0)),
                    cache_read_tokens=int(data.get("cache_read_tokens", 0)),
                    cost_usd=data.get("cost_usd"),
                    priced_usd=data.get("priced_usd"),
                    trace_path=data.get("trace_path"),
                    plan=data.get("plan"),
                    error_kind=data.get("error_kind"),
                )
            except Exception:
                log.exception("unreadable result.json for %s", job.run_id)
        return RunResult(
            ok=False,
            summary=f"sandbox exited {exit_code} without a result file; tail:\n{logs[-1500:]}",
        )


#: The name it had while the SDK was the only harness in the container.
AgentSDKBackend = SandboxBackend
