# yaaf-plugin-api

The contract between [yaaf](https://github.com/dominicplouffe/yaaf) and a
factory plugin: a new kind of project, installed from its own GitHub repo.
The guide is in `docs/PLUGINS.md`, the brief to hand an AI agent is in
`docs/PLUGIN_AGENT_BRIEF.md`, and the design is in `docs/plans/factory-plugins.md`.

Outside a yaaf checkout, install it with:

```bash
pip install "git+https://github.com/dominicplouffe/yaaf#subdirectory=packages/yaaf_plugin_api"
```

What is here (API 1.0):

| Module | What it defines |
|---|---|
| `yaaf_plugin_api.types` | What a hook is given (`ProjectSnapshot`, `Upload`, …) and returns (`Accept`, `Refuse`, `Owed`, `Page`, …) |
| `yaaf_plugin_api.hooks` | `FactoryPlugin`, the `@hook.*` decorators, and the checks that code matches its manifest and returns usable values |
| `yaaf_plugin_api.manifest` | `yaaf-plugin.toml`: parsing and validation |
| `yaaf_plugin_api.view` | The project-home view: components, and checks for the declaration and the data |
| `yaaf_plugin_api.requirements` | The conformance requirements (MIN-*, EXT-*) as data |
| `yaaf_plugin_api.report` | The conformance report for a checkout: static, or `full_check` |
| `yaaf_plugin_api.conformance` | The conformance suite, given any way to call a hook |
| `yaaf_plugin_api.client` | A local plugin host, and the runner for your own tests |
| `yaaf_plugin_api.fixtures`, `.corpus` | What the suite asks with |
| `yaaf_plugin_api.scaffold` | `new`: a plugin repo that already passes the suite, with `AGENTS.md` |
| `yaaf_plugin_api.spec` | `spec`: the whole contract as one JSON document |
| `schema/*.json` | JSON Schemas for the manifest and the view, for editors and AI authors |

Start a new plugin (the result already passes `check --full`; `new --help` lists every flag):

```bash
python -m yaaf_plugin_api new my-plugin --id my-plugin --artifact report.json --review --view --lead
```

Check a plugin checkout:

```bash
python -m yaaf_plugin_api check path/to/plugin          # files, manifest, hooks
python -m yaaf_plugin_api check path/to/plugin --full   # + the conformance suite and your tests
```

`--full` runs the same conformance suite yaaf runs at install
(`yaaf_plugin_api.conformance`), through a local plugin host
(`yaaf_plugin_api.client`), plus your own `tests/` with pytest. What remains
`pending` after a clean `--full` is what only an install can decide: MIN-02
(names unique on that box), MIN-03 (the hashed build) and MIN-07 (your roles
and crews against that box's library rules).

The fixtures the suite reads are described in `yaaf_plugin_api/fixtures.py`,
and the fuzz corpus every hook is asked with is in `yaaf_plugin_api/corpus.py`.

The report marks each requirement `pass`, `fail`, `n/a` or `pending`.
`pending` means only the sandboxed build, the plugin host, the SDK harness or
core's own attacks can decide that requirement. No checkout alone can make a
plugin `ready`.

`examples/grant-factory` is a small, complete plugin to start from.

The package has no dependencies, on purpose: it is imported into every
plugin's sandbox before the plugin is.
