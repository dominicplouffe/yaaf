#!/usr/bin/env sh
set -e
# Migrations are idempotent; both api and runner may race, alembic locks.
#
# This deliberately does NOT tolerate failure. It used to end in
# `|| echo "WARN: ..."`, which meant a box whose migrations never applied
# booted anyway: /healthz returned 200 (it only proves the process is alive),
# auth worked (it never touches the database), and then every real request
# died on `relation "projects" does not exist`. A fresh install read as
# "the app is broken" rather than "the schema was never created".
#
# Exiting is the louder and more recoverable choice: compose restarts us
# (`restart: unless-stopped`), so a transient failure retries until the
# database is genuinely there, and a permanent one crash-loops visibly
# instead of serving 500s that look like application bugs.
alembic upgrade head
exec "$@"
