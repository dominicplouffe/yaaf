"""Invariant selftests: what the Docker deployment exposes, and what it reads.

Two regressions this holds:

- **Published ports bypass ufw.** Docker writes its own iptables rules, so a
  port published without a host address is reachable from anywhere the box is.
  The optional MinIO store was published that way, with a password from the
  repository, which made it an open S3 endpoint on any server that enabled it.
- **deploy/.env reached the app only through a hand-kept list.** Any variable
  not named in the compose file (the MCP redirect hosts, the S3 settings, the
  worker name) was silently dropped, with nothing to say so.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def test_every_published_port_names_a_host_address(services):
    loose = [f"{name}: {port}"
             for name, svc in services.items()
             for port in svc.get("ports", [])
             # "host:published:container" has three parts; two means 0.0.0.0.
             if isinstance(port, str) and port.split("#")[0].strip().count(":") < 2]
    assert not loose, f"published on every interface: {loose}"


def test_minio_has_no_password_from_the_repository(services):
    password = services["minio"]["environment"]["MINIO_ROOT_PASSWORD"]
    assert password == "${MINIO_ROOT_PASSWORD:-}", (
        "MinIO must take its password from deploy/.env, never a default in this file")


def test_deploy_script_generates_the_minio_password():
    script = (COMPOSE.parent / "deploy_single_server.sh").read_text()
    assert "MINIO_ROOT_PASSWORD=$(openssl rand" in script


@pytest.mark.parametrize("name", ["api", "runner"])
def test_app_containers_read_all_of_deploy_env(services, name):
    files = services[name].get("env_file", [])
    paths = [f["path"] if isinstance(f, dict) else f for f in files]
    assert ".env" in paths, f"{name} must read deploy/.env as a whole"


def test_saved_copies_of_deploy_env_are_never_committed():
    """The rotate, restore and backup scripts save deploy/.env beside itself
    (deploy/.env.<date>.bak and the like). Each copy holds a master key."""
    ignore = (COMPOSE.parents[1] / ".gitignore").read_text().splitlines()
    assert "deploy/.env.*" in ignore and "!deploy/.env.example" in ignore


# ── published images ────────────────────────────────────────────────────────


def test_the_scripts_pull_what_ci_publishes():
    """The deploy scripts pull <registry>/<name>:sha-<commit> and fall back to a
    build when it is missing. A rename on either side would not fail anything:
    every install would just build from source again, slowly, forever."""
    root = COMPOSE.parents[1]
    workflow = yaml.safe_load((root / ".github" / "workflows" / "images.yml").read_text())
    published = {i["name"] for i in workflow["jobs"]["build"]["strategy"]["matrix"]["image"]}
    assert published == {"yaaf", "yaaf-sandbox"}
    joined = "\n".join(workflow["jobs"]["publish"]["steps"][-1]["run"].splitlines())
    assert '-t "$IMAGE:sha-${GITHUB_SHA}"' in joined

    common = (COMPOSE.parent / "_common.sh").read_text()
    assert '"${YAAF_IMAGE_REGISTRY}/yaaf:sha-${commit}"' in common
    assert '"${YAAF_IMAGE_REGISTRY}/yaaf-sandbox:sha-${commit}"' in common


def test_both_app_services_run_one_image_named_per_stack(services):
    assert services["api"]["image"] == services["runner"]["image"] == \
        "yaaf-${YAAF_STACK:-deploy}:local"
