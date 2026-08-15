"""The artifact store: durable bytes behind one small interface.

Evidence snapshots, run traces, and agent deliverables must outlive both the
sandbox (destroyed after every run) and the workspace (runner-local). They land
here, addressed by key, and every row that references one stores the returned
*uri* — so a row written under one backend stays readable after a switch.

Backends, chosen by environment:

- :class:`LocalStore` (default) — plain files under ``settings().artifacts_root``,
  uri ``file://<abs path>``. Correct for the single-box compose deployment,
  where api and runner share the artifacts volume.
- :class:`S3Store` — any S3-compatible endpoint (MinIO, R2, AWS), selected iff
  ``YAAF_ARTIFACT_S3_ENDPOINT`` is set; uri ``s3://<bucket>/<key>``. boto3 is
  imported lazily inside the class so the base install never needs it
  (``pip install 'yaaf[s3]'``).
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from yaaf.core.config import settings


class ArtifactStore(ABC):
    """put/get/exists on opaque keys. Keys are forward-slash paths like
    ``traces/run_x.jsonl``; ``put_bytes`` returns the durable uri to record."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> str: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        """Raises FileNotFoundError when the key is absent."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalStore(ArtifactStore):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"artifact key escapes the store root: {key!r}")
        return path

    def put_bytes(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"file://{path}"

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3Store(ArtifactStore):
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str):
        # Lazy: the base install never needs boto3 (`pip install 'yaaf[s3]'`).
        import boto3
        from botocore.exceptions import ClientError

        self._client_error = ClientError
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        # MinIO starts empty; make first use self-serve. Where the credentials
        # cannot create buckets this quietly does nothing and the first put
        # raises instead — a loud failure at write time beats one at boot.
        try:
            self.client.head_bucket(Bucket=bucket)
        except Exception:
            with contextlib.suppress(Exception):
                self.client.create_bucket(Bucket=bucket)

    def put_bytes(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return f"s3://{self.bucket}/{key}"

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self._client_error as exc:
            raise FileNotFoundError(f"s3://{self.bucket}/{key}") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self._client_error:
            return False


@lru_cache
def store() -> ArtifactStore:
    """The process-wide store. S3 iff the endpoint is configured."""
    cfg = settings()
    if cfg.artifact_s3_endpoint:
        return S3Store(
            endpoint=cfg.artifact_s3_endpoint,
            bucket=cfg.artifact_s3_bucket,
            access_key=cfg.artifact_s3_access_key,
            secret_key=cfg.artifact_s3_secret_key,
        )
    return LocalStore(cfg.artifacts_root)


def is_store_uri(value: str) -> bool:
    return value.startswith(("file://", "s3://"))


def read_uri(uri: str) -> bytes:
    """Fetch a store uri's bytes regardless of which backend wrote it — old
    ``file://`` rows must stay readable after a switch to S3, and vice versa.
    Raises FileNotFoundError when the artifact is absent."""
    if uri.startswith("file://"):
        return Path(uri[len("file://"):]).read_bytes()
    if uri.startswith("s3://"):
        bucket, _, key = uri[len("s3://"):].partition("/")
        backend = store()
        if not isinstance(backend, S3Store):
            raise FileNotFoundError(
                f"{uri} was written by an S3 backend but none is configured "
                "(set YAAF_ARTIFACT_S3_*)"
            )
        try:
            return backend.client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except backend._client_error as exc:
            raise FileNotFoundError(uri) from exc
    raise ValueError(f"not an artifact store uri: {uri!r}")
