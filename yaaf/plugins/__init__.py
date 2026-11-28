"""Factory plugins: new kinds of project from a GitHub repo (docs/plans/factory-plugins.md).

The contract lives in `packages/yaaf_plugin_api`. This package is core's half:
`host` starts and talks to the sandboxed plugin hosts. Later steps add the
registry, the hook points, the conformance suite and the GitHub install.
"""
