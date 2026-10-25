# Sandbox image: what an agent run gets, and nothing more.
# Node (for the Claude Code CLI the Agent SDK drives) + Python + the entry script.
FROM node:22-slim

# Same reasoning as the control-plane image: a 502 from a package CDN is not a
# reason a deploy should fail. This image is built first by both deploy
# scripts, so a flake here stops the run before it starts.
ENV PIP_RETRIES=10 \
    PIP_TIMEOUT=60 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# gawk is not a convenience. Agents write success tests in awk, the base image
# resolves awk to mawk, and mawk silently does not support POSIX interval
# expressions ({2,5}) — so a checker using one rejects every input it is ever
# given, with a message about the data. One did, for 22 hours, against a file
# that was correct the whole time. Installing gawk moves the Debian alternative
# so /usr/bin/awk is the awk everyone assumes they are writing for.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git curl bash ca-certificates gawk jq \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code
# The Codex CLI, pinned: its flags, config keys and session file were read off
# this version (yaaf/runner/codex_cli_entry.py), and a CLI that moves under a
# driver is a run that fails in a new way. Update deliberately, re-recording
# tests/fixtures/harnesses/codex/.
RUN npm install -g @openai/codex@0.162.1
# The OpenCode CLI, pinned for the same reason (yaaf/runner/opencode_cli_entry.py,
# tests/fixtures/harnesses/opencode/). Its plugin package is pre-seeded per run
# at this version, which is what keeps it from fetching one at startup: move
# both together.
RUN npm install -g opencode-ai@1.19.0
# The Gemini CLI, pinned likewise (yaaf/runner/gemini_cli_entry.py,
# tests/fixtures/harnesses/gemini/). Two of its behaviours the driver leans on
# are observed rather than documented — --skip-trust keeping a workspace's
# settings out, and which model names it rewrites — so re-check both on a bump.
RUN npm install -g @google/gemini-cli@0.63.0

# pandas and duckdb are baked in because the alternative to having them is an
# agent reading ten thousand rows into its own context to count them. duckdb
# queries csv/parquet/json with SQL straight off disk; pandas is what a model
# reaches for by reflex. Both land in /opt/venv, which PATH puts first — so
# the `python3` an agent runs through Bash is this interpreter. The mcp pin
# bounds client-API drift for data_fetch, which imports it; the anthropic pin
# does the same for yaaf's loop on Anthropic's wire, which also imports the
# HTTP client (httpx2) that SDK major speaks. Every other wire is httpx. (The network is
# open, so an agent can pip install more at run time — these are baked so the
# common cases cost zero setup.)
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        claude-agent-sdk "anthropic>=1.13,<2" httpx pandas duckdb "mcp<2" playwright
ENV PATH="/opt/venv/bin:${PATH}"

# The browser lane (CEO Charter, stage E): Chromium via playwright, for the
# services that have no API — signups, dashboards, checkouts. Installed with
# system deps at build time because a browser is the one dependency an agent
# cannot cheaply fetch mid-run. PLAYWRIGHT_BROWSERS_PATH is world-readable so
# the non-root agent user finds it.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN /opt/venv/bin/playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers
# ENV alone is not enough: /etc/profile OVERWRITES PATH (it does not prepend),
# so every LOGIN shell loses the venv — and success tests run under `bash -lc`.
# Without this, an agent's own `python3 -c "import pandas"` check fails with a
# ModuleNotFoundError that describes nothing about what is actually wrong.
RUN printf 'PATH="/opt/venv/bin:$PATH"\nexport PATH\n' > /etc/profile.d/00-venv.sh

# Non-root: the workspace mount is the only writable surface that matters.
RUN useradd -m -u 10001 agent
# File-shaped credentials need no image support: the runner mounts a tmpfs at
# /run/yaaf (sandbox.CREDENTIALS_TMPFS) and the entry script makes its own 0700
# directory inside it. Nothing here, on purpose — a key must never be in a layer.
COPY yaaf/runner/sandbox_entry.py /opt/yaaf/sandbox_entry.py
COPY yaaf/runner/chat_entry.py /opt/yaaf/chat_entry.py
# The Claude CLI harness and the stdio MCP server it starts for the yaaf tools.
# The CLI itself is the one installed above for the Agent SDK to drive.
COPY yaaf/runner/claude_cli_entry.py /opt/yaaf/claude_cli_entry.py
COPY yaaf/runner/yaaf_tools.py /opt/yaaf/yaaf_tools.py
# yaaf's own agent loop (yaaf_loop), its entry, and every provider's wire —
# the same wire.py the control plane talks to providers with, so a request the
# Founder can make is a request a seat makes. No CLI underneath.
COPY yaaf/runner/loop_harness.py /opt/yaaf/loop_harness.py
COPY yaaf/runner/loop_entry.py /opt/yaaf/loop_entry.py
COPY yaaf/runtimes/wire.py /opt/yaaf/wire.py
# The Codex CLI harness; it starts yaaf_tools.py above for the yaaf tools.
COPY yaaf/runner/codex_cli_entry.py /opt/yaaf/codex_cli_entry.py
# The OpenCode CLI harness; it too starts yaaf_tools.py for the yaaf tools.
COPY yaaf/runner/opencode_cli_entry.py /opt/yaaf/opencode_cli_entry.py
# The Gemini CLI harness; it starts yaaf_tools.py for the yaaf tools too.
COPY yaaf/runner/gemini_cli_entry.py /opt/yaaf/gemini_cli_entry.py
USER agent
WORKDIR /workspace
