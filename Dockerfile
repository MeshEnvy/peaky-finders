# Unified Peaky runtime: SPLAT + splatter + Python (dev and production targets).
#
# Dev (no baked src — mount peaky_finders/):
#   docker build --target dev -t peaky:dev .
#
# Production:
#   docker build --target latest -t peaky:latest .

FROM debian:bookworm-slim AS splat-build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    libbz2-dev \
    zlib1g-dev \
    sed \
    && rm -rf /var/lib/apt/lists/*

COPY splat/ /tmp/splat/

WORKDIR /tmp/splat
RUN sed -i.bak 's/-march=\$cpu/-march=native/g' build \
    && chmod +x configure build install utils/build \
    && printf "8\n0\n" | ./configure \
    && test -x splat \
    && test -x utils/srtm2sdf

RUN mkdir -p /opt/splat \
    && cp splat /opt/splat/splat \
    && (test -f splat-hd && cp splat-hd /opt/splat/splat-hd || cp splat /opt/splat/splat-hd) \
    && cp utils/srtm2sdf utils/srtm2sdf-hd /opt/splat/ \
    && chmod +x /opt/splat/*


FROM rust:bookworm AS splatter-build

WORKDIR /src
COPY splatter/Cargo.toml splatter/Cargo.lock ./
COPY splatter/src ./src
RUN cargo build --locked --release


FROM python:3.11-slim-bookworm AS python-deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app/peaky_finders

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && ln -sf /root/.local/bin/poetry /usr/local/bin/poetry

COPY peaky_finders/pyproject.toml peaky_finders/poetry.lock ./
RUN poetry install --no-root --only main --no-interaction


FROM python:3.11-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    SPLAT_PATH=/opt/splat \
    SPLAT_CACHE=/.peaky/splat_cache \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /project

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=splat-build /opt/splat /opt/splat
COPY --from=splatter-build /src/target/release/splatter /usr/local/bin/splatter
COPY --from=python-deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-deps /usr/local/bin/poetry /usr/local/bin/poetry
COPY --from=python-deps /root/.local /root/.local

COPY docker/peaky-entrypoint.sh /usr/local/bin/peaky-entrypoint
RUN chmod +x /usr/local/bin/peaky-entrypoint

ENTRYPOINT ["/usr/local/bin/peaky-entrypoint"]
CMD ["--help"]


FROM runtime-base AS dev


FROM runtime-base AS latest

COPY peaky_finders/ /app/peaky_finders/
WORKDIR /app/peaky_finders
RUN poetry install --only main --no-interaction
WORKDIR /project
