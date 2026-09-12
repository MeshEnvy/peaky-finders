# peaky-map

Static coverage overlay export for meshenvy.org `/map`:

1. Load deployed fleet sites from `nodes.yaml` + `sites.yaml`
2. Generate or reuse splatter viewsheds (`.peaky/cache/viewsheds/`)
3. Georeference native `splat.png` viewsheds (plasma colormap) and mosaic with GDAL
4. Emit metadata GeoJSON + Web Mercator XYZ tiles

## CLI

```bash
peaky map export /path/to/peaky-nevada --out-dir /path/to/meshenvy.org/static
```

## Semantics

- **`meshenvy_meta.model`**: `splatter_v5` (reads project `config.yaml` simulation block)
- **Hub-bridge progress**: generic `hub_bridge` crate; Nevada hubs and the legacy JSON key `silver_triangle` live in `meshenvy.rs` for the site contract only
- **Gap-fill**: disabled (`close_iterations=0`) so the public map does not bridge real RF gaps
- **Corridor P2P closure**: not exported (privacy + separate metric); future optional stat from splatter pair API without site coordinates
