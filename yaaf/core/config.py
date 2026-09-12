"""Process configuration, read once from the environment.

Everything operational lives here (DB URL, keys, ports). Everything *governed*
lives in the database — per-project constitutions and configs — never in env vars.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YAAF_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://yaaf:yaaf@localhost:5432/yaaf"

    # Bearer token for the admin/chairman API until full user auth lands (M3).
    admin_token: str = "dev-admin-token"

    # Fernet key (urlsafe base64, 32 bytes) for envelope-encrypting secrets.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    master_key: str = ""

    # Base URL agents use to reach the platform API from inside sandboxes.
    api_base_url: str = "http://localhost:8080"

    # Browser-facing origin of this install (e.g. https://yaaf.example.com) —
    # used to build the OAuth redirect URI when connecting an OAuth-protected
    # MCP data source. Unset, the redirect URI is derived from the incoming
    # request, which is fine for local dev but reports plain http behind a
    # TLS-terminating proxy; production installs should set it, and OAuth
    # providers generally refuse non-https redirect URIs anyway.
    public_base_url: str = ""

    # Hosts an MCP client may be redirected back to after consent. Dynamic
    # client registration is an unauthenticated write endpoint by design, so
    # this is the wall around it: anyone may register, nobody may name a
    # redirect target outside this list. Localhost is here for the MCP
    # inspector, which is how you debug a connector that will not connect.
    mcp_redirect_hosts: str = "claude.ai,claude.com,localhost,127.0.0.1"

    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # Runner
    worker_name: str = "worker-1"
    # Which stack on this box owns the containers we launch. Every sandbox and
    # chat container is labelled with it, and every lookup filters on it, so a
    # second stack (a worktree testing a branch beside the live one) can never
    # adopt, rename or reap containers it does not own. The default matches the
    # implicit compose project name, so a single-stack box sees no change.
    #
    # This is not belt-and-braces: chat containers are found by project id, and
    # a test stack restored from a dump of the live database has the SAME
    # project ids. Without this label its supervisor reads the live session as
    # a zombie and removes it.
    stack: str = "deploy"
    # "docker" (isolation; default) or "subprocess" (dev/CI only — NO isolation)
    sandbox_mode: str = "docker"
    # A task's success test can be a command string an *agent* wrote. Under
    # sandbox_mode="subprocess" that string runs on the runner host, which
    # mounts the Docker socket — so the platform refuses it unless an operator
    # says otherwise in so many words. Docker mode ignores this: there the
    # command runs in a throwaway container with no credentials, which is the
    # whole point of the mode.
    allow_host_success_tests: bool = False
    sandbox_image: str = "yaaf-sandbox:latest"
    sandbox_network: str = "yaaf_sandbox"
    workspaces_root: str = "/var/lib/yaaf/workspaces"
    artifacts_root: str = "/var/lib/yaaf/artifacts"

    # S3-compatible artifact store (MinIO, R2, AWS). Setting the endpoint
    # switches yaaf.core.artifacts from local files under artifacts_root to S3;
    # requires the optional boto3 dependency (`pip install 'yaaf[s3]'`).
    artifact_s3_endpoint: str = ""
    artifact_s3_bucket: str = "yaaf-artifacts"
    artifact_s3_access_key: str = ""
    artifact_s3_secret_key: str = ""


    # Chairman notifications (approval nags, budget pauses). Unset = log only.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    chairman_webhook_url: str = ""  # generic JSON POST (Slack-compatible "text" field)

    # Dispatcher/janitor cadences (seconds). LISTEN/NOTIFY is a latency hint on
    # top of these polling floors, never a replacement for them.
    dispatch_poll_s: float = 2.0
    janitor_poll_s: float = 15.0
    runner_poll_s: float = 2.0
    lease_heartbeat_s: float = 20.0
    lease_ttl_s: float = 90.0


@lru_cache
def settings() -> Settings:
    return Settings()
