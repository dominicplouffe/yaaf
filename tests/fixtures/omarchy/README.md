# Omarchy's own files, for testing yaaf's Omarchy layer against them

Copied unchanged from [basecamp/omarchy](https://github.com/basecamp/omarchy)
at commit `14abc0762ce38547d1912061969df58962f6f10a` (2026-11-06, version
`4.0.0.alpha`), under its MIT license (`LICENSE` here). The tests in
`tests/selftest_invariants/test_omarchy_layer.py` run yaaf's output through
them, so "Omarchy accepts this" is checked with Omarchy's own code rather
than with a copy of its rules written from memory:

| File | From | Used for |
|---|---|---|
| `omarchy-plugin-validate` | `bin/omarchy-plugin-validate` | the bar plugin yaaf writes must pass it (needs bash and jq) |
| `MenuModel.js` | `shell/plugins/menu/MenuModel.js` | the menu file yaaf edits must parse with Omarchy's own parser (needs node) |
| `omarchy-menu.jsonc` | `config/omarchy/extensions/omarchy-menu.jsonc` | the user menu file a fresh Omarchy install starts with |
| `bindings.lua` | `config/hypr/bindings.lua` | the user bindings file a fresh Omarchy install starts with |
| `themes/<name>.toml` | `themes/<name>/colors.toml` | every theme Omarchy ships: the dashboard's tokens worked out from each must hold the stylesheet's contrast |

When Omarchy changes one of these, copy the new one here and see what fails.
