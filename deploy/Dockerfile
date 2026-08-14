# Control-plane image: API server (with dispatcher+janitor threads) and runner.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY yaaf ./yaaf
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .

# Apply migrations, then run whatever command compose gives us.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["yaaf-server"]
