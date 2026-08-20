//! Disk cache for rendered MapLibre DEM tiles (hillshade + terrarium PNG).

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TileKind {
    Hillshade,
    Terrarium,
}

impl TileKind {
    fn dir_name(self) -> &'static str {
        match self {
            Self::Hillshade => "hillshade",
            Self::Terrarium => "terrarium",
        }
    }
}

/// Bump when tile encoding or completeness rules change (invalidates stale PNGs).
pub fn cache_root(mirror_root: &Path) -> PathBuf {
    mirror_root.join(".map_tiles/v3")
}

fn tile_path(mirror_root: &Path, kind: TileKind, z: u32, x: u32, y: u32) -> PathBuf {
    cache_root(mirror_root)
        .join(kind.dir_name())
        .join(z.to_string())
        .join(x.to_string())
        .join(format!("{y}.png"))
}

pub fn read(mirror_root: &Path, kind: TileKind, z: u32, x: u32, y: u32) -> Result<Option<Vec<u8>>> {
    let path = tile_path(mirror_root, kind, z, x, y);
    if !path.is_file() {
        return Ok(None);
    }
    let bytes = fs::read(&path).with_context(|| format!("read cached tile {}", path.display()))?;
    if bytes.len() < 8 || !bytes.starts_with(&[0x89, 0x50, 0x4E, 0x47]) {
        let _ = fs::remove_file(&path);
        return Ok(None);
    }
    Ok(Some(bytes))
}

pub fn write(mirror_root: &Path, kind: TileKind, z: u32, x: u32, y: u32, png: &[u8]) -> Result<()> {
    let path = tile_path(mirror_root, kind, z, x, y);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("create cache dir {}", parent.display()))?;
    }
    let tmp = path.with_extension("png.tmp");
    {
        let mut f = fs::File::create(&tmp)
            .with_context(|| format!("create cache temp {}", tmp.display()))?;
        f.write_all(png)
            .with_context(|| format!("write cache temp {}", tmp.display()))?;
        f.sync_all().ok();
    }
    fs::rename(&tmp, &path).with_context(|| format!("rename cache tile {}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_cache_tile() {
        let dir = std::env::temp_dir().join(format!("dem-tile-cache-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let png = vec![0x89, 0x50, 0x4E, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3];
        write(&dir, TileKind::Hillshade, 10, 165, 395, &png).unwrap();
        let back = read(&dir, TileKind::Hillshade, 10, 165, 395)
            .unwrap()
            .expect("cached");
        assert_eq!(back, png);
        let _ = fs::remove_dir_all(&dir);
    }
}
