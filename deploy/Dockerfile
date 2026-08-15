# Control-plane image: API server (with dispatcher+janitor threads), runner,
# and the built web UI served by the API under /app.

FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY yaaf ./yaaf
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .

COPY --from=webbuild /web/dist /app/web/dist
ENV YAAF_WEB_DIST=/app/web/dist

# Apply migrations, then run whatever command compose gives us.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["yaaf-server"]
