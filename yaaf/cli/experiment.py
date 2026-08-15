"""`yaaf experiment`: the M2 baseline-experiment harness.

Runs the gate from docs/M2_PROTOCOL.md: org arm (CEO + lead + employees) vs a
single agent under *identical* governance, goal, and budget. This module only
orchestrates and measures — every rule the arms live under is the same server-
side policy engine as production; the harness holds no power an arm could
borrow.

Flow: `init` provisions both arms per goal pair (paused; the chairman starts
them within 24h of each other) → the window runs → `export` bundles each arm's
deliverables under sealed random labels → `judge` scores the blind bundles
(judge model passes + recorded human scores) → `decide` unseals, computes the
preregistered composites, and writes docs/M2_RESULT.md. The protocol document
is frozen before `init`; this code implements it, it does not interpret it.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path

import httpx
import typer

experiment_app = typer.Typer(no_args_is_help=True, add_completion=False)

COUNTABLE = ("verified", "not_applicable")
RUBRIC_DIMENSIONS = ("fitness", "craft", "utility", "coherence")  # each 0-25

JUDGE_PROMPT = """\
You are scoring one anonymized work bundle from a fixed rubric. You do not know
who or what produced it, and you must not guess. Score ONLY what is in front of
you; volume is not quality.

The goal this bundle was produced against:
{goal}

Rubric — score each dimension 0-25 (integers):
- fitness: does the work achieve the stated goal as specified?
- craft: correctness, polish, absence of filler or fabrication.
- utility: would an unaffiliated stranger plausibly use this?
- coherence: does the body of work hang together, or is it scattered output?

Bundle contents:
{bundle}

Reply with ONLY a JSON object: {{"fitness": n, "craft": n, "utility": n,
"coherence": n, "rationale": "<two sentences>"}}
"""


def _client() -> httpx.Client:
    from yaaf.cli.main import _client as base_client

    return base_client()


def _die(message: str) -> None:
    typer.echo(message)
    raise typer.Exit(1)


def _load_spec(spec_file: Path) -> dict:
    spec = json.loads(spec_file.read_text())
    for required in ("experiment", "chairman_email", "pairs", "weights", "window_days",
                     "budget_tokens", "evidence_target"):
        if required not in spec:
            _die(f"spec is missing '{required}'")
    if round(sum(spec["weights"].values()), 6) != 1.0:
        _die("rubric weights must sum to 1.0 — they are frozen in the protocol")
    return spec


def _arm_slug(spec: dict, pair: dict, arm: str) -> str:
    return f"{spec['experiment']}-{pair['key']}-{arm}"


def _project_by_slug(client: httpx.Client, slug: str) -> dict | None:
    for project in client.get("/observe/projects").json():
        if project["slug"] == slug:
            return project
    return None


# ── init ────────────────────────────────────────────────────────────────────────


@experiment_app.command()
def init(spec_file: Path) -> None:
    """Provision both arms for every goal pair (created paused — the chairman
    starts each pair's arms within 24h of each other, per protocol)."""
    spec = _load_spec(spec_file)
    constitution_base = {
        "monthly_token_cap": spec["budget_tokens"],
        **({"monthly_usd_cap": spec["budget_usd"]} if "budget_usd" in spec else {}),
    }
    with _client() as client:
        for pair in spec["pairs"]:
            for arm in ("single", "org"):
                slug = _arm_slug(spec, pair, arm)
                if _project_by_slug(client, slug):
                    typer.echo(f"{slug}: already exists, skipping")
                    continue
                body = {
                    "slug": slug,
                    "name": f"[{spec['experiment']}] {pair['key']} ({arm})",
                    "goal": pair["goal"],
                    "chairman_email": spec["chairman_email"],
                    "billing_mode": spec.get("billing_mode", "api_key"),
                    "controlled_domains": pair.get("controlled_domains", {}).get(arm, []),
                    "constitution": {**constitution_base, **pair.get("constitution", {})},
                    "config": {"experiment": {"name": spec["experiment"],
                                              "pair": pair["key"], "arm": arm}},
                }
                if arm == "single":
                    # One brain that plans AND executes: the CEO is the sole
                    # member (and thus effective lead) of the only team.
                    body["solo"] = True
                    body["teams"] = [{"role_slug": pair["worker_role"],
                                      "name": "Solo", "employees": []}]
                else:
                    body["teams"] = [{
                        "role_slug": pair["worker_role"],
                        "name": "Build",
                        "employees": ["Lead", "Alpha", "Beta"],
                        "lead": "Lead",
                    }]
                response = client.post("/admin/projects", json=body)
                if response.status_code >= 400:
                    _die(f"{slug}: {response.status_code} {response.text[:300]}")
                typer.echo(f"{slug}: created (paused). Start with `yaaf start {slug}`.")
    typer.echo("\nBoth arms of a pair must start within 24h of each other. "
               "Freeze docs/M2_PROTOCOL.md (commit) before starting anything.")


# ── status ──────────────────────────────────────────────────────────────────────


def _arm_metrics(client: httpx.Client, project_id: str) -> dict:
    dash = client.get(f"/observe/projects/{project_id}").json()
    evidence = client.get(f"/observe/projects/{project_id}/evidence",
                          params={"limit": 500}).json()
    ledger = client.get(f"/observe/projects/{project_id}/ledger",
                        params={"limit": 500}).json()
    runs = client.get(f"/observe/projects/{project_id}/runs", params={"limit": 200}).json()
    countable = [e for e in evidence
                 if e["kind"] == "external" and e["verification"] in COUNTABLE]
    tokens = sum((e["tokens_in"] or 0) + (e["tokens_out"] or 0) for e in ledger)
    started = [r["started_at"] for r in runs if r["started_at"]]
    first_run = min(started) if started else None
    last_evidence = max((e["at"] for e in countable), default=None)
    return {
        "status": dash["status"],
        "evidence_verified": len(countable),
        "tokens": tokens,
        "first_run_at": first_run,
        "last_verified_evidence_at": last_evidence,
        "ceo_judgment": dash.get("ceo_judgment"),
    }


@experiment_app.command()
def status(spec_file: Path) -> None:
    """Live metric table per pair and arm (E, C, T inputs, conversion)."""
    spec = _load_spec(spec_file)
    with _client() as client:
        for pair in spec["pairs"]:
            typer.echo(f"\n== {pair['key']} (target N={spec['evidence_target']}, "
                       f"budget {spec['budget_tokens']:,} tokens)")
            for arm in ("single", "org"):
                slug = _arm_slug(spec, pair, arm)
                project = _project_by_slug(client, slug)
                if project is None:
                    typer.echo(f"  {arm:>6}: NOT PROVISIONED")
                    continue
                m = _arm_metrics(client, project["id"])
                conversion = (m["ceo_judgment"] or {}).get("conversion")
                typer.echo(
                    f"  {arm:>6}: {m['status']:>8}  E={m['evidence_verified']:>3}  "
                    f"C={m['tokens']:>12,}  first_run={m['first_run_at'] or '-'}  "
                    f"last_evidence={m['last_verified_evidence_at'] or '-'}  "
                    f"conversion={conversion if conversion is not None else '-'}"
                )


# ── export (blind bundles) ──────────────────────────────────────────────────────


@experiment_app.command()
def export(
    spec_file: Path,
    out: Path = typer.Option(Path("m2-bundles"), "--out"),
    workspaces_root: Path = typer.Option(None, "--workspaces-root",
                                         help="Defaults to $YAAF_WORKSPACES_ROOT or "
                                              "/var/lib/yaaf/workspaces"),
) -> None:
    """Bundle each arm's deliverables under sealed random labels for blind
    judging. The label→arm mapping is written separately — do not open it
    until scores are recorded."""
    spec = _load_spec(spec_file)
    root = workspaces_root or Path(
        os.environ.get("YAAF_WORKSPACES_ROOT", "/var/lib/yaaf/workspaces")
    )
    out.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, dict] = {}
    with _client() as client:
        for pair in spec["pairs"]:
            for arm in ("single", "org"):
                slug = _arm_slug(spec, pair, arm)
                project = _project_by_slug(client, slug)
                if project is None:
                    _die(f"{slug} is not provisioned; nothing to export")
                label = f"bundle-{secrets.token_hex(4)}"
                bundle = out / label
                bundle.mkdir()
                evidence = client.get(f"/observe/projects/{project['id']}/evidence",
                                      params={"limit": 500}).json()
                # Strip anything that could identify the arm before judging.
                (bundle / "evidence.json").write_text(json.dumps(
                    [{"description": e["description"], "verification": e["verification"],
                      "kind": e["kind"], "at": e["at"]} for e in evidence], indent=2))
                workspace = root / slug
                if workspace.is_dir():
                    shutil.copytree(
                        workspace, bundle / "workspace",
                        ignore=shutil.ignore_patterns(".yaaf", ".git", "node_modules"),
                    )
                mapping[label] = {"pair": pair["key"], "arm": arm, "project_id": project["id"]}
                typer.echo(f"{label}  <-  (sealed)")
    sealed = out / "mapping.sealed.json"
    sealed.write_text(json.dumps(mapping, indent=2))
    manifest = {"experiment": spec["experiment"],
                "labels": sorted(mapping), "goals": {p["key"]: p["goal"]
                                                     for p in spec["pairs"]}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    typer.echo(f"\n{len(mapping)} bundles in {out}/. Judge with "
               f"`yaaf experiment judge {out}`; keep {sealed.name} unopened until then.")


# ── judging ─────────────────────────────────────────────────────────────────────


def _bundle_text(bundle: Path, max_bytes: int = 120_000) -> str:
    parts = [f"EVIDENCE:\n{(bundle / 'evidence.json').read_text()}"]
    workspace = bundle / "workspace"
    if workspace.is_dir():
        listing = sorted(str(p.relative_to(workspace))
                         for p in workspace.rglob("*") if p.is_file())
        parts.append("FILES:\n" + "\n".join(listing[:400]))
        budget = max_bytes
        for rel in listing:
            path = workspace / rel
            try:
                text = path.read_text(errors="strict")[:8000]
            except (UnicodeDecodeError, OSError):
                continue  # binaries are visible in FILES, not quoted
            chunk = f"\n--- {rel} ---\n{text}"
            if budget - len(chunk) < 0:
                parts.append("\n[bundle truncated at judge context limit]")
                break
            parts.append(chunk)
            budget -= len(chunk)
    return "\n".join(parts)


def _judge_once(model: str, goal: str, bundle_text: str, api_key: str) -> dict:
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": 500,
            "temperature": 0,
            "messages": [{"role": "user",
                          "content": JUDGE_PROMPT.format(goal=goal, bundle=bundle_text)}],
        },
        timeout=300,
    )
    response.raise_for_status()
    text = response.json()["content"][0]["text"]
    start, end = text.find("{"), text.rfind("}")
    scores = json.loads(text[start:end + 1])
    for dimension in RUBRIC_DIMENSIONS:
        if not isinstance(scores.get(dimension), int) or not 0 <= scores[dimension] <= 25:
            raise ValueError(f"judge returned invalid {dimension}: {scores.get(dimension)}")
    return scores


@experiment_app.command()
def judge(
    bundles: Path,
    model: str = typer.Option("claude-opus-5", "--model"),
    passes: int = typer.Option(3, "--passes"),
) -> None:
    """Run the judge-model passes over every blind bundle (requires
    ANTHROPIC_API_KEY). Human scores are added with `yaaf experiment score`."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or _die("set ANTHROPIC_API_KEY to judge")
    manifest = json.loads((bundles / "manifest.json").read_text())
    scores_path = bundles / "scores.json"
    scores = json.loads(scores_path.read_text()) if scores_path.exists() else {}
    # The goal is shown per-pair, but labels are blind: judge every bundle
    # against every goal? No — the manifest maps labels to nothing. Judge with
    # the union of goals so the pairing stays sealed.
    goals = "\n".join(f"- {g}" for g in manifest["goals"].values())
    for label in manifest["labels"]:
        bundle_text = _bundle_text(bundles / label)
        entry = scores.setdefault(label, {"model": [], "human": None})
        while len(entry["model"]) < passes:
            result = _judge_once(model, goals, bundle_text, api_key)
            entry["model"].append(result)
            typer.echo(f"{label} pass {len(entry['model'])}/{passes}: "
                       f"{ {d: result[d] for d in RUBRIC_DIMENSIONS} }")
            scores_path.write_text(json.dumps(scores, indent=2))
    typer.echo(f"\nModel passes complete → {scores_path}. Record your own pass with:\n"
               f"  yaaf experiment score {bundles} <label> <fitness> <craft> <utility> <coherence>")


@experiment_app.command()
def score(bundles: Path, label: str, fitness: int, craft: int, utility: int,
          coherence: int) -> None:
    """Record the human (chairman) rubric pass for one blind bundle."""
    scores_path = bundles / "scores.json"
    scores = json.loads(scores_path.read_text())
    if label not in scores:
        _die(f"unknown label {label!r}")
    for name, value in zip(RUBRIC_DIMENSIONS, (fitness, craft, utility, coherence),
                           strict=True):
        if not 0 <= value <= 25:
            _die(f"{name} must be 0-25")
    scores[label]["human"] = {"fitness": fitness, "craft": craft,
                              "utility": utility, "coherence": coherence}
    scores_path.write_text(json.dumps(scores, indent=2))
    typer.echo(f"{label}: human pass recorded")


# ── decide ──────────────────────────────────────────────────────────────────────


def _quality(entry: dict) -> float:
    """Q 0-100: equal-weight mean of the judge passes and the human pass."""
    passes = [sum(p[d] for d in RUBRIC_DIMENSIONS) for p in entry["model"]]
    if entry.get("human"):
        passes.append(sum(entry["human"][d] for d in RUBRIC_DIMENSIONS))
    return sum(passes) / len(passes)


def _elapsed_days(first_run: str | None, last_evidence: str | None) -> float | None:
    if not first_run or not last_evidence:
        return None
    delta = datetime.fromisoformat(last_evidence) - datetime.fromisoformat(first_run)
    return max(delta.total_seconds() / 86_400, 0.0)


@experiment_app.command()
def decide(
    spec_file: Path,
    bundles: Path = typer.Option(Path("m2-bundles"), "--bundles"),
    out: Path = typer.Option(Path("docs/M2_RESULT.md"), "--out"),
) -> None:
    """Unseal the mapping, compute the preregistered composites, apply the
    decision rule, and write the result document. Run only after judging."""
    spec = _load_spec(spec_file)
    weights = spec["weights"]
    mapping = json.loads((bundles / "mapping.sealed.json").read_text())
    scores = json.loads((bundles / "scores.json").read_text())
    n_target, budget, window = (spec["evidence_target"], spec["budget_tokens"],
                                spec["window_days"])

    rows, pair_winners = [], {}
    with _client() as client:
        for pair in spec["pairs"]:
            composites = {}
            for arm in ("single", "org"):
                label = next((lbl for lbl, m in mapping.items()
                              if m["pair"] == pair["key"] and m["arm"] == arm), None)
                if label is None or label not in scores or not scores[label]["model"]:
                    _die(f"{pair['key']}/{arm}: unjudged — judge before deciding")
                metrics = _arm_metrics(client, mapping[label]["project_id"])
                quality = _quality(scores[label])
                elapsed = _elapsed_days(metrics["first_run_at"],
                                        metrics["last_verified_evidence_at"])
                composite = (
                    weights["evidence"] * min(metrics["evidence_verified"] / n_target, 1.0)
                    + weights["quality"] * quality / 100
                    + weights["cost"] * max(1 - metrics["tokens"] / budget, 0.0)
                    + weights["time"] * (max(1 - elapsed / window, 0.0) if elapsed else 0.0)
                )
                composites[arm] = composite
                rows.append({"pair": pair["key"], "arm": arm, "label": label,
                             "E": metrics["evidence_verified"], "Q": round(quality, 1),
                             "C": metrics["tokens"], "T_days": elapsed,
                             "S": round(composite, 4)})
            margin = composites["org"] - composites["single"]
            pair_winners[pair["key"]] = (
                "tie" if abs(margin) < spec.get("tie_margin", 0.03)
                else "org" if margin > 0 else "single"
            )

    org_wins = sum(1 for w in pair_winners.values() if w == "org")
    decided = len([w for w in pair_winners.values() if w != "tie"])
    org_beats_single = decided > 0 and org_wins > decided / 2 and org_wins >= 1
    verdict = ("ORG BEATS SINGLE — M3+ proceeds under the plan."
               if org_beats_single else
               "ORG DOES NOT BEAT SINGLE — per the kill condition, yaaf-as-org-framework "
               "is killed; salvage path: governance harness for single agents.")

    lines = [
        "# M2 result — baseline experiment", "",
        f"Experiment `{spec['experiment']}`, decided {datetime.now().date()}. "
        f"Protocol: docs/M2_PROTOCOL.md (frozen before runs).", "",
        "| pair | arm | label | E | Q | C (tokens) | T (days) | S |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        t_days = f"{row['T_days']:.1f}" if row["T_days"] is not None else "-"
        lines.append(f"| {row['pair']} | {row['arm']} | {row['label']} | {row['E']} | "
                     f"{row['Q']} | {row['C']:,} | {t_days} | {row['S']} |")
    lines += ["", "Pair winners: "
              + ", ".join(f"{k}: **{v}**" for k, v in pair_winners.items()), "",
              "## Verdict", "", verdict, ""]
    out.write_text("\n".join(lines))
    typer.echo("\n".join(lines))
    typer.echo(f"\nWritten to {out}. Record it in the boardrooms and update "
               f"docs/PLAN.md's status appendix.")
