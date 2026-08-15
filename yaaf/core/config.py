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

    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # Runner
    worker_name: str = "worker-1"
    # "docker" (isolation; default) or "subprocess" (dev/CI only — NO isolation)
    sandbox_mode: str = "docker"
    sandbox_image: str = "yaaf-sandbox:latest"
    sandbox_network: str = "yaaf_sandbox"
    egress_proxy_url: str = "http://proxy:3128"
    workspaces_root: str = "/var/lib/yaaf/workspaces"
    artifacts_root: str = "/var/lib/yaaf/artifacts"

    # S3-compatible artifact store (MinIO, R2, AWS). Setting the endpoint
    # switches yaaf.core.artifacts from local files under artifacts_root to S3;
    # requires the optional boto3 dependency (`pip install 'yaaf[s3]'`).
    artifact_s3_endpoint: str = ""
    artifact_s3_bucket: str = "yaaf-artifacts"
    artifact_s3_access_key: str = ""
    artifact_s3_secret_key: str = ""

    # Egress auto-apply: when set, the runner watches the generated proxy
    # config for changes, writes it here, and HUPs the named container —
    # config edits in the UI reach squid without any CLI step. Empty = manual
    # `yaaf egress-sync` (multi-box setups).
    proxy_generated_dir: str = ""
    proxy_container: str = ""

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
