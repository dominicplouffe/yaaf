# Proof: before and after

The same eight screens, light and dark, 1440 px wide, full page.

- `before/` — `main` at 856e0ee, before the brand work.
- `after/` — the three brand PRs applied.

Files are named `<screen>-<theme>.png`: `login`, `home`, `inbox`, `team`, `chat`,
`results`, `details-roadmap`, `details-spend`, `settings`.

Both sets come from the repository's own demo seed
(`python tests/e2e/demo.py <dir> --serve=<port>`), signed in as
`dominic@example.com`, on the project *Read The Filings*. The two builds ran on
separately seeded databases, so clock times and a few seeded amounts differ
between a pair; nothing else does. `docs/screenshots/` was regenerated from the
same seed and the "after" build (dark, 1440 px at 2x).
