# Control-plane image: API server (with dispatcher+janitor threads), runner,
# and the built web UI served by the API under /app.

FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

# A deploy that dies because one CDN request came back 502 is a deploy that
# fails for reasons having nothing to do with the code being shipped. Ten
# retries and a real timeout cost nothing on a healthy network.
ENV PIP_RETRIES=10 \
    PIP_TIMEOUT=60 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# The control plane runs one agent of its own. The Founder — the conversation
# that designs a company before one exists — runs on the Claude Code CLI in
# this process, the same way every agent in a company runs on it inside a
# sandbox, so that a subscription token works in both places. Without the CLI
# here the Founder fails on its first message, and only in production: the
# checkout a developer tests in has one on PATH.
COPY --from=webbuild /usr/local/bin/node /usr/local/bin/node
COPY --from=webbuild /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version

WORKDIR /app

# Dependencies in a layer of their own, keyed on pyproject.toml alone — the
# same shape the web build above already had. Copying the source first meant
# every commit invalidated this layer and re-downloaded the whole dependency
# set from PyPI, which is slow on a good day and, on a bad one, hands PyPI a
# veto over whether you can deploy at all. A stub package is all setuptools
# needs to build the metadata and resolve the dependency list.
COPY pyproject.toml README.md ./
RUN mkdir -p yaaf && touch yaaf/__init__.py \
    && pip install --no-cache-dir --upgrade "setuptools>=68" wheel \
    && pip install --no-cache-dir --no-build-isolation . \
    && rm -rf yaaf yaaf.egg-info

# The real source. --no-deps because the layer above already installed them,
# and --no-build-isolation because otherwise pip fetches setuptools into a
# fresh build env every time — which is the exact request that failed. Between
# them, the layer that rebuilds on every commit touches the network not at all.
COPY yaaf ./yaaf
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

COPY --from=webbuild /web/dist /app/web/dist
ENV YAAF_WEB_DIST=/app/web/dist

# Apply migrations, then run whatever command compose gives us.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["yaaf-server"]
