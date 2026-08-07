# Unified Peaky runtime: splatter (PyO3) + Python (dev and production targets).
#
# Dev (mount peaky_finders/ + splatter/ at runtime; image holds cached deps):
#   docker build --target dev -t peaky:dev .
#
# Production:
#   docker build --target latest -t peaky-finders .

FROM rust:bookworm AS rust-toolchain

# --- Splatter: cache Cargo registry fetch on lockfile only ---
FROM rust:bookworm AS splatter-deps
WORKDIR /build/splatter
COPY splatter/Cargo.toml splatter/Cargo.lock ./
RUN mkdir -p src \
    && printf 'pub fn dummy() {}\n' > src/lib.rs \
    && printf 'fn main() {}\n' > src/main.rs
RUN cargo fetch

# --- Splatter: rebuild wheel only when Rust sources / lock change ---
FROM splatter-deps AS splatter-wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --break-system-packages "maturin>=1.5,<2"
COPY splatter/ ./
RUN maturin build --release -o /wheels --features extension-module

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
    && ln -sf /root/.local/bin/poetry /usr/local/bin/poetry \
    && poetry self add poetry-plugin-export

COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup

# Splatter wheel (path dep satisfied without compiling again in this layer).
COPY --from=splatter-wheel /wheels /wheels
RUN pip install --break-system-packages /wheels/splatter*.whl

# Python deps: export lockfile (skip path splatter — already installed from wheel above).
COPY peaky_finders/pyproject.toml peaky_finders/poetry.lock /app/peaky_finders/
WORKDIR /app/peaky_finders
RUN poetry export --only main --without-hashes -o /tmp/requirements.txt \
    && grep -viE '^splatter|^$' /tmp/requirements.txt > /tmp/requirements-no-splatter.txt \
    && pip install --break-system-packages -r /tmp/requirements-no-splatter.txt


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
COPY --from=splatter-wheel /wheels /opt/peaky/wheels
COPY --from=splatter-wheel /build/splatter /app/splatter

COPY docker/peaky-entrypoint.sh /usr/local/bin/peaky-entrypoint
COPY docker/dev-bootstrap.sh /usr/local/bin/peaky-dev-bootstrap
RUN chmod +x /usr/local/bin/peaky-entrypoint /usr/local/bin/peaky-dev-bootstrap

ENTRYPOINT ["/usr/local/bin/peaky-entrypoint"]
CMD ["--help"]


FROM runtime-base AS dev

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH="/usr/local/cargo/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --break-system-packages "maturin>=1.5,<2"

COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup

# Bake peaky_finders so plain serve works without mounting; mount overrides at runtime.
COPY peaky_finders/ /app/peaky_finders/
WORKDIR /app/peaky_finders
RUN pip install --break-system-packages -e . --no-deps


FROM runtime-base AS latest

COPY peaky_finders/ /app/peaky_finders/
COPY --from=splatter-wheel /wheels /wheels
WORKDIR /app/peaky_finders
RUN pip install --break-system-packages /wheels/splatter*.whl \
    && poetry export --only main --without-hashes -o /tmp/requirements.txt \
    && grep -viE '^splatter|^$' /tmp/requirements.txt > /tmp/requirements-no-splatter.txt \
    && pip install --break-system-packages -r /tmp/requirements-no-splatter.txt \
    && pip install --break-system-packages -e . --no-deps
WORKDIR /.peaky
