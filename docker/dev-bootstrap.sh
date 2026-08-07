#!/usr/bin/env bash
# Dev container bootstrap: persistent caches + stamp files — skip work when unchanged.
set -euo pipefail

PEAKY_CACHE="${PEAKY_CACHE:-/.peaky}"
STAMP_DIR="${PEAKY_CACHE}/build-stamps"
mkdir -p "$STAMP_DIR"

export PATH="/usr/local/cargo/bin:/usr/local/bin:${PATH}"

splatter_sources_hash() {
  [ -d /app/splatter/src ] || return 1
  find /app/splatter/src /app/splatter/Cargo.toml /app/splatter/Cargo.lock -type f 2>/dev/null \
    | LC_ALL=C sort | xargs sha256sum 2>/dev/null | sha256sum | awk '{print $1}'
}

splatter_api_ok() {
  python3 - <<'PY' 2>/dev/null
from splatter._core import Session
assert hasattr(Session, "linkable_binned_peaks")
PY
}

ensure_splatter() {
  if [ "${PEAKY_SKIP_SPLATTER:-0}" = 1 ] || [ ! -f /app/splatter/Cargo.toml ]; then
    return 0
  fi

  local hash=""
  hash="$(splatter_sources_hash)" || true

  if [ "${PEAKY_FORCE_SPLATTER_REBUILD:-0}" != 1 ] && [ -n "$hash" ] && [ -f "$STAMP_DIR/splatter.${hash}" ]; then
    if splatter_api_ok; then
      return 0
    fi
  fi

  echo "peaky: building splatter (release)…" >&2
  local pip_bin="${1:-pip}"
  "$pip_bin" install -q "maturin>=1.5,<2"
  "$pip_bin" uninstall -y splatter 2>/dev/null || true
  maturin build --release -m /app/splatter/Cargo.toml -o /tmp/peaky-wheels --features extension-module
  "$pip_bin" install --force-reinstall --no-deps /tmp/peaky-wheels/splatter*.whl

  hash="$(splatter_sources_hash)"
  : >"$STAMP_DIR/splatter.${hash}"
}

ensure_peaky_editable() {
  pip install -q -e /app/peaky_finders --no-deps
}

ensure_poetry_dev_deps() {
  local lock_hash
  lock_hash="$(sha256sum /app/peaky_finders/poetry.lock | awk '{print $1}')"
  if [ -f "$STAMP_DIR/poetry-dev.${lock_hash}" ]; then
    return 0
  fi
  echo "peaky: poetry install (dev deps)…" >&2
  cd /app/peaky_finders
  poetry install --no-interaction --no-root
  : >"$STAMP_DIR/poetry-dev.${lock_hash}"
}

case "${PEAKY_BOOTSTRAP_MODE:-serve}" in
  serve)
    ensure_splatter pip
    ensure_peaky_editable
    ;;
  test)
    ensure_poetry_dev_deps
    ensure_splatter /app/peaky_finders/.venv/bin/pip
    ;;
  run)
    cd /app/peaky_finders && poetry install --no-interaction
    ;;
esac
