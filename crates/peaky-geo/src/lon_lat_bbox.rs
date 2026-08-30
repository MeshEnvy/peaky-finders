//! WGS-84 bounding box used to clip land reads for seek and finder.

use anyhow::{bail, Result};
use geo::algorithm::bounding_rect::BoundingRect;
use geo::{Geometry, LineString, Polygon};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LonLatBBox {
    pub west: f64,
    pub south: f64,
    pub east: f64,
    pub north: f64,
}

impl LonLatBBox {
    pub fn new(west: f64, south: f64, east: f64, north: f64) -> Self {
        Self {
            west: west.min(east),
            south: south.min(north),
            east: west.max(east),
            north: south.max(north),
        }
    }

    pub fn from_tuple(bbox: (f64, f64, f64, f64)) -> Self {
        Self::new(bbox.0, bbox.1, bbox.2, bbox.3)
    }

    pub fn from_geometry(geom: &Geometry<f64>) -> Option<Self> {
        let rect = geom.bounding_rect()?;
        Some(Self::new(rect.min().x, rect.min().y, rect.max().x, rect.max().y))
    }

    pub fn padded(self, deg: f64) -> Self {
        let pad = deg.max(0.0);
        Self {
            west: self.west - pad,
            south: self.south - pad,
            east: self.east + pad,
            north: self.north + pad,
        }
    }

    pub fn intersects(self, other: Self) -> bool {
        self.west <= other.east
            && self.east >= other.west
            && self.south <= other.north
            && self.north >= other.south
    }

    pub fn expand(&mut self, other: Self) {
        self.west = self.west.min(other.west);
        self.south = self.south.min(other.south);
        self.east = self.east.max(other.east);
        self.north = self.north.max(other.north);
    }

    /// Parse `west,south,east,north`.
    pub fn parse_csv(raw: &str) -> Result<Self> {
        let parts: Vec<&str> = raw.split(',').map(str::trim).collect();
        if parts.len() != 4 {
            bail!("bbox must be west,south,east,north");
        }
        let west: f64 = parts[0]
            .parse()
            .map_err(|_| anyhow::anyhow!("bbox must be west,south,east,north"))?;
        let south: f64 = parts[1]
            .parse()
            .map_err(|_| anyhow::anyhow!("bbox must be west,south,east,north"))?;
        let east: f64 = parts[2]
            .parse()
            .map_err(|_| anyhow::anyhow!("bbox must be west,south,east,north"))?;
        let north: f64 = parts[3]
            .parse()
            .map_err(|_| anyhow::anyhow!("bbox must be west,south,east,north"))?;
        if west >= east || south >= north {
            bail!("bbox west<east and south<north required");
        }
        Ok(Self::new(west, south, east, north))
    }

    /// Expand each edge to a 0.001° grid so nearby viewports share a cache key.
    pub fn quantize_outward(self, decimals: u32) -> Self {
        let scale = 10_f64.powi(decimals as i32);
        Self {
            west: (self.west * scale).floor() / scale,
            south: (self.south * scale).floor() / scale,
            east: (self.east * scale).ceil() / scale,
            north: (self.north * scale).ceil() / scale,
        }
    }

    pub fn as_polygon(self) -> Geometry<f64> {
        Geometry::Polygon(Polygon::new(
            LineString::from(vec![
                geo::Coord {
                    x: self.west,
                    y: self.south,
                },
                geo::Coord {
                    x: self.east,
                    y: self.south,
                },
                geo::Coord {
                    x: self.east,
                    y: self.north,
                },
                geo::Coord {
                    x: self.west,
                    y: self.north,
                },
                geo::Coord {
                    x: self.west,
                    y: self.south,
                },
            ]),
            vec![],
        ))
    }

    pub fn cache_key(self) -> String {
        format!(
            "{:.3}_{:.3}_{:.3}_{:.3}",
            self.west, self.south, self.east, self.north
        )
    }

    pub fn contains_bbox(self, other: Self) -> bool {
        self.west <= other.west
            && self.south <= other.south
            && self.east >= other.east
            && self.north >= other.north
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn padded_bbox_grows_all_sides() {
        let b = LonLatBBox::new(-115.0, 35.0, -114.0, 36.0).padded(0.1);
        assert!((b.west + 115.1).abs() < 1e-9);
        assert!((b.east + 113.9).abs() < 1e-9);
        assert!((b.south - 34.9).abs() < 1e-9);
        assert!((b.north - 36.1).abs() < 1e-9);
    }

    #[test]
    fn disjoint_bboxes_do_not_intersect() {
        let az = LonLatBBox::new(-115.0, 34.0, -113.0, 36.0);
        let or = LonLatBBox::new(-124.0, 42.0, -116.0, 46.0);
        assert!(!az.intersects(or));
        assert!(az.intersects(az.padded(0.01)));
    }

    #[test]
    fn parse_csv_and_quantize_outward() {
        let b = LonLatBBox::parse_csv("-115.1234,35.01,-114.0001,36.999").expect("csv");
        assert!((b.west + 115.1234).abs() < 1e-9);
        let q = b.quantize_outward(3);
        assert!(q.contains_bbox(b));
        assert!(q.west <= b.west);
        assert!(q.east >= b.east);
    }
}
