"""`python -m yaaf.rehearsal <spec.json>`.

Its own process on purpose. The drill re-points the process-global engine in
:mod:`yaaf.core.db` at a throwaway SQLite file, which is exactly what must
never happen inside a running API server — so the CLI and the admin route both
reach the drill through this entry point rather than by importing it.
"""

from __future__ import annotations

from yaaf.rehearsal import main

if __name__ == "__main__":
    raise SystemExit(main())
