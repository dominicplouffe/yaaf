"""Invariant selftest: a run's staged inputs belong to that run.

The workspace is per-*project* and shared by every agent in it, so the files an
agent's container reads at startup cannot live at fixed names. They did. Three
runs starting in the same second overwrote each other and all three died
reading half a JSON document — and a crash is the *lucky* outcome, because with
kinder timing a run reads another agent's pack, complete with its task, its
notices and its messages, and says nothing at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from yaaf.runner.backends.agent_sdk import AgentSDKBackend
from yaaf.runner.types import RunJob


def _job(run_id: str, workspace: str, agent: str, size: int) -> RunJob:
    return RunJob(
        run_id=run_id, project_id="proj_shared", agent_id=agent, kind="agent_run",
        trigger={"type": "wake"}, task_id=None, attempt=1,
        system_prompt=f"You are {agent}." + "." * size,
        context_pack={"run_id": run_id, "you": {"name": agent}, "pad": "x" * size},
        workspace_dir=workspace,
    )


def _stage(job: RunJob) -> None:
    """Everything AgentSDKBackend.execute does before launching a container."""
    workspace = Path(job.workspace_dir)
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / f"system_prompt-{job.run_id}.txt").write_text(job.system_prompt)
    (staging / f"context-{job.run_id}.json").write_text(
        json.dumps(job.context_pack, default=str))


def test_concurrent_runs_do_not_share_staged_inputs(tmp_path):
    """Two agents staging into one workspace get two distinct sets of files,
    and each reads back exactly what was written for it."""
    workspace = str(tmp_path / "shared-project")
    # Different sizes on purpose: equal-length writes would hide a truncating
    # overwrite, which is what produced "Extra data: line 1 column 5458".
    first = _job("run_aaa", workspace, "Cleo", size=64)
    second = _job("run_bbb", workspace, "CEO", size=4096)
    _stage(first)
    _stage(second)

    staging = tmp_path / "shared-project" / ".yaaf"
    for job in (first, second):
        prompt = (staging / f"system_prompt-{job.run_id}.txt").read_text()
        pack = json.loads((staging / f"context-{job.run_id}.json").read_text())
        assert prompt == job.system_prompt
        assert pack["run_id"] == job.run_id
        assert pack["you"]["name"] == job.context_pack["you"]["name"]

    assert not (staging / "context.json").exists(), "no unkeyed name to race on"
    assert not (staging / "system_prompt.txt").exists()


def test_the_entry_script_and_the_runner_agree_on_the_staged_names():
    """The reader is baked into the sandbox image and the writer ships in the
    control plane. If these two drift, every run dies at startup — so pin the
    contract in a test rather than in a deploy note."""
    entry = Path("yaaf/runner/sandbox_entry.py").read_text()
    backend = Path("yaaf/runner/backends/agent_sdk.py").read_text()
    for name in ("system_prompt-", "context-"):
        assert f'{name}{{RUN_ID}}' in entry, f"entry script must read {name}<run id>"
        assert f'{name}{{job.run_id}}' in backend, f"backend must write {name}<run id>"


def test_staged_inputs_are_cleared_after_the_run(tmp_path):
    """Run-keyed names no longer overwrite each other, so without a sweep they
    accumulate one pair per run in a workspace that outlives the project."""
    from yaaf.runner.daemon import _clear_staged_inputs

    workspace = str(tmp_path / "shared-project")
    job = _job("run_ccc", workspace, "Cleo", size=32)
    _stage(job)
    staging = tmp_path / "shared-project" / ".yaaf"
    assert (staging / f"context-{job.run_id}.json").exists()

    _clear_staged_inputs(job)
    assert not (staging / f"context-{job.run_id}.json").exists()
    assert not (staging / f"system_prompt-{job.run_id}.txt").exists()
    _clear_staged_inputs(job)  # idempotent: a finalize must never fail on this


def test_backend_is_the_only_writer_of_staged_inputs():
    """Guards the fix itself: AgentSDKBackend is where staging happens, and it
    must not reintroduce a fixed name beside the keyed one."""
    backend = Path("yaaf/runner/backends/agent_sdk.py").read_text()
    assert '"context.json"' not in backend
    assert '"system_prompt.txt"' not in backend
    assert AgentSDKBackend is not None


def test_a_run_that_staged_nothing_stashes_nothing(tmp_path):
    """A success check never stages a context pack, and the workspace it runs
    in belongs to every agent on the project. So "no file for this run" must
    mean an empty audit record, never the nearest file that happens to exist —
    that is how a check run got recorded against another agent's context from
    the previous day. A confident wrong record is worse than none."""
    from yaaf.runner.daemon import _stash_context

    workspace = tmp_path / "shared-project"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True)
    # A neighbour's pack, and the legacy unkeyed names, both sitting right there.
    _stage(_job("run_neighbour", str(workspace), "Cleo", size=32))
    (staging / "context.json").write_text(json.dumps({"run_id": "run_ancient"}))
    (staging / "system_prompt.txt").write_text("a prompt from another run")

    check = RunJob(
        run_id="run_check", project_id="proj_shared", agent_id=None,
        kind="success_check", trigger={"type": "success_check"}, task_id="task_x",
        attempt=1, workspace_dir=str(workspace), check_command="echo checking",
    )
    assert _stash_context(check) is None
