# Peaky Finders — serve (and find) from a container.
#
#   docker build -t peaky:latest .
#
#   docker run --rm -p 8080:8080 \
#     -v /path/to/peaky_home:/peaky_home \
#     -v "${SPLAT_CACHE:-$HOME/.cache/splat}:/splat_cache" \
#     peaky:latest
#
# Other commands: docker run --rm … peaky:latest find path --help

FROM rust:bookworm AS builder
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY splatter ./splatter
COPY crates ./crates
COPY cmd ./cmd
COPY assets ./assets
RUN cargo build --locked --release -p peaky

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/target/release/peaky /usr/local/bin/peaky

ENV PEAKY_HOME=/peaky_home \
    SPLAT_CACHE=/splat_cache

EXPOSE 8080 9847

ENTRYPOINT ["/usr/local/bin/peaky"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
