FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DISPLAY=:99 \
    TOKEN_DIR=/data/tokens

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates chromium fonts-liberation fonts-noto-cjk \
        tini xvfb x11-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY main.py config.example.yaml ./
COPY register/ ./register/
COPY util/ ./util/
RUN mkdir -p /data/tokens

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || exit 1

# Keep the display running; registration is started explicitly with docker exec.
ENTRYPOINT ["tini", "--"]
CMD ["Xvfb", ":99", "-screen", "0", "1920x1080x24", "-nolisten", "tcp", "-ac"]
