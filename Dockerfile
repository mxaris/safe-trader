# syntax=docker/dockerfile:1

# ---- build: create a wheel so the runtime image has no build tooling ----
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY safe_trader ./safe_trader
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- runtime ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install /wheels/*.whl

# Never trade as root.
RUN useradd --create-home --uid 10001 trader \
 && mkdir -p /app/state /app/data \
 && chown -R trader:trader /app
WORKDIR /app
USER trader

# config.yaml is mounted at runtime (never baked into the image: it is yours,
# and credentials come from environment variables, not files).
VOLUME ["/app/state"]

# Healthy while the runner keeps saving state (it does so after every successful poll).
HEALTHCHECK --interval=60s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import os,sys,time; p='/app/state/state.json'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 900 else 1)"

# Exec form so the bot is PID 1 and receives SIGTERM for a clean shutdown.
ENTRYPOINT ["safe-trader"]
CMD ["run", "-c", "/app/config.yaml"]
