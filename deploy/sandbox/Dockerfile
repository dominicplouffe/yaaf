# Sandbox image: what an agent run gets, and nothing more.
# Node (for the Claude Code CLI the Agent SDK drives) + Python + the entry script.
FROM node:22-slim

# Same reasoning as the control-plane image: a 502 from a package CDN is not a
# reason a deploy should fail. This image is built first by both deploy
# scripts, so a flake here stops the run before it starts.
ENV PIP_RETRIES=10 \
    PIP_TIMEOUT=60 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git curl bash ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir claude-agent-sdk httpx
ENV PATH="/opt/venv/bin:${PATH}"

# Non-root: the workspace mount is the only writable surface that matters.
RUN useradd -m -u 10001 agent
COPY yaaf/runner/sandbox_entry.py /opt/yaaf/sandbox_entry.py
COPY yaaf/runner/chat_entry.py /opt/yaaf/chat_entry.py
USER agent
WORKDIR /workspace
