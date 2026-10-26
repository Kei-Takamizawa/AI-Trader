# The operator pins the base image digest at build time.
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY config/ config/
RUN uv sync --frozen --no-dev && useradd --create-home app && chown -R app:app /app
ENV PATH="/app/.venv/bin:$PATH"
ARG GIT_COMMIT
ENV AITRADER_GIT_COMMIT=$GIT_COMMIT
USER app
ENTRYPOINT ["aitrader", "paper", "daily"]
