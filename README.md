# Peaky Finders

LoRa mesh RF planning on a local map — sites, goals, viewsheds, mesh links. **Docker only.**

## Quickstart

```bash
docker run --rm -p 8080:8080 \
  -v "$HOME/.peaky:/.peaky" \
  peaky-finders serve
```
## CLI

KMZ export. `-w` must be the project dir for preset commands (CLI reads `./config.yaml` from cwd):

```bash
docker run --rm -v "$HOME/.peaky:/.peaky" \
  -w /.peaky/projects/my-region peaky-finders build
```

## Developers

Clone with submodules. From repo root: `./peaky serve`, `./peaky test`. See [MEMORY.md](MEMORY.md).

## Shell helper

Optional shortcut — add to your shell profile:

```bash
peaky() {
  local workdir="/.peaky"
  case "$PWD" in
    "$HOME/.peaky"|"$HOME/.peaky"/*) workdir="/.peaky${PWD#"$HOME/.peaky"}" ;;
  esac
  docker run --rm -v "$HOME/.peaky:/.peaky" -w "$workdir" peaky-finders "$@"
}
```

Then: `peaky serve`, `peaky new my-region`, `cd ~/.peaky/projects/my-region && peaky build`.
