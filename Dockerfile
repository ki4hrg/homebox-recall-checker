# uv from its official image, pinned. A named stage, so Dependabot's docker ecosystem
# reads it as a FROM line and bumps it.
FROM ghcr.io/astral-sh/uv:0.13.8 AS uv

# Python 3.13, as in .python-version and pyproject.toml's requires-python.
FROM python:3.13-slim

COPY --from=uv /uv /bin/uv

# PYTHONUNBUFFERED: log lines reach `docker logs` as they are written.
# UV_*: compile bytecode at build time, copy (not link) into the venv, keep no cache in
# the layer, and use the image's Python, never a downloaded one.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    DATA_DIR=/data

# Runs as this user, not root; /data (the SQLite database) is the only directory it writes.
RUN useradd --system --uid 10001 --home /app app \
    && mkdir -p /data && chown app /data

WORKDIR /app

# Dependencies first, exactly as locked, so a code-only change reuses this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

# Then the package itself (hatchling reads README.md for the metadata), installed into the
# venv rather than linked to the source tree.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

# The commit this image was built from, reported by /api/health and at startup. CI passes it
# (the standards' image.yml); a local build without --build-arg reports null. Declared after
# the dependency layers, so a new commit doesn't invalidate them.
ARG GIT_REVISION=""
ENV GIT_REVISION=${GIT_REVISION}

USER app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)" || exit 1

ENTRYPOINT ["recall-checker"]
CMD ["serve"]
