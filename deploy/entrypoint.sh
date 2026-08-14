#!/usr/bin/env sh
set -e
# Migrations are idempotent; both api and runner may race, alembic locks.
alembic upgrade head || echo "WARN: alembic upgrade failed (already applied or db not ready)"
exec "$@"
