"""The real backend: Claude Agent SDK inside a Docker sandbox.

The host side (this file) stages inputs into the workspace, launches the
sandbox container, and reads the result back. The model runs *inside* the
container (see ``sandbox_entry.py``): its only credentials are the per-run
yaaf token and the project's billing credential, its only network is the
egress proxy, and its only platform surface is the agent HTTP API.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from yaaf.runner.types import RunJob, RunResult

log = logging.getLogger("yaaf.agent_sdk")


class AgentSDKBackend:
    def execute(self, job: RunJob, stop_event=None) -> RunResult:
        from yaaf.runner.sandbox import run_agent_container

        workspace = Path(job.workspace_dir)
        staging = workspace / ".yaaf"
        staging.mkdir(parents=True, exist_ok=True)
        # The sandbox runs as an unprivileged user; the runner (often root in a
        # container) stages these dirs, so open them up or nothing comes back.
        workspace.chmod(0o777)
        staging.chmod(0o777)
        (staging / "system_prompt.txt").write_text(job.system_prompt)
        (staging / "context.json").write_text(json.dumps(job.context_pack, default=str))
        result_path = staging / f"result-{job.run_id}.json"
        if result_path.exists():
            result_path.unlink()

        env = {
            "YAAF_RUN_ID": job.run_id,
            "YAAF_API_BASE_URL": job.api_base_url,
            "YAAF_API_TOKEN": job.api_token,
            "YAAF_MODEL": job.model,
            "YAAF_EFFORT": job.effort,
            "YAAF_TOOL_PERMISSIONS": json.dumps(job.tool_permissions),
            **job.billing_env,
        }
        exit_code, logs = run_agent_container(
            workspace_dir=str(workspace), env=env, timeout_s=job.timeout_s,
            stop_event=stop_event, proxy_url=job.egress_proxy_url,
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
                    cost_usd=data.get("cost_usd"),
                    trace_path=data.get("trace_path"),
                )
            except Exception:
                log.exception("unreadable result.json for %s", job.run_id)
        return RunResult(
            ok=False,
            summary=f"sandbox exited {exit_code} without a result file; tail:\n{logs[-1500:]}",
        )
