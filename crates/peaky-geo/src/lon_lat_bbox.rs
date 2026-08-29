//! WGS-84 bounding box used to clip land reads for seek and finder.

use geo::algorithm::bounding_rect::BoundingRect;
use geo::Geometry;

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
}
