# Unified Peaky runtime: splatter (PyO3) + Python (dev and production targets).
#
# Dev (no baked src — mount peaky_finders/):
#   docker build --target dev -t peaky:dev .
#
# Production:
#   docker build --target latest -t peaky-finders .

FROM rust:bookworm AS rust-toolchain


FROM python:3.11-slim-bookworm AS python-deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH="/usr/local/cargo/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && ln -sf /root/.local/bin/poetry /usr/local/bin/poetry

COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup

COPY splatter/ /app/splatter/
COPY peaky_finders/pyproject.toml peaky_finders/poetry.lock /app/peaky_finders/
WORKDIR /app/peaky_finders
RUN poetry install --no-root --only main --no-interaction


FROM python:3.11-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PEAKY_HOME=/.peaky \
    SPLAT_CACHE=/.peaky/splat_cache \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /.peaky

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-deps /usr/local/bin/poetry /usr/local/bin/poetry
COPY --from=python-deps /root/.local /root/.local
COPY --from=python-deps /app/splatter /app/splatter

COPY docker/peaky-entrypoint.sh /usr/local/bin/peaky-entrypoint
RUN chmod +x /usr/local/bin/peaky-entrypoint

ENTRYPOINT ["/usr/local/bin/peaky-entrypoint"]
CMD ["--help"]


FROM runtime-base AS dev

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH="/usr/local/cargo/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup


FROM runtime-base AS latest

COPY peaky_finders/ /app/peaky_finders/
WORKDIR /app/peaky_finders
RUN poetry install --only main --no-interaction
WORKDIR /.peaky
