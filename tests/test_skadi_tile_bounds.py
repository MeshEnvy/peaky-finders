"""Unit tests for Skadi 1-degree tile enumeration over EPSG:4326 bboxes."""

from __future__ import annotations

import unittest

from peaky_finders.skadi_dem import (
    iter_skadi_tile_names_for_wgs84_bounds,
    skadi_tile_wgs84_bounds,
)


class SkadiTileWgs84BoundsTests(unittest.TestCase):
    def test_nw_us_convention(self) -> None:
        self.assertEqual(skadi_tile_wgs84_bounds("N36W117.hgt.gz"), (-117.0, 36.0, -116.0, 37.0))
        self.assertEqual(skadi_tile_wgs84_bounds("N37W115.hgt"), (-115.0, 37.0, -114.0, 38.0))

    def test_southern_eastern(self) -> None:
        self.assertEqual(skadi_tile_wgs84_bounds("S38E148.hgt.gz"), (148.0, -38.0, 149.0, -37.0))


class IterSkadiTileNamesForWgs84BoundsTests(unittest.TestCase):
    def test_single_cell_negative_lon_us(self) -> None:
        self.assertEqual(
            iter_skadi_tile_names_for_wgs84_bounds(-117.51, 37.1, -117.1, 37.51),
            ["N37W118.hgt.gz"],
        )

    def test_two_lon_columns_same_lat_row(self) -> None:
        self.assertEqual(
            iter_skadi_tile_names_for_wgs84_bounds(-117.9, 37.05, -116.05, 37.94),
            ["N37W118.hgt.gz", "N37W117.hgt.gz"],
        )

    def test_west_us_span_corners_geo_order(self) -> None:
        names = iter_skadi_tile_names_for_wgs84_bounds(-120.5, 36.15, -114.2, 37.91)
        self.assertEqual(len(names), 14)
        self.assertEqual(names[0], "N36W121.hgt.gz")
        self.assertEqual(names[-1], "N37W115.hgt.gz")

    def test_southern_hemisphere(self) -> None:
        names = iter_skadi_tile_names_for_wgs84_bounds(148.5, -37.85, 149.1, -37.05)
        self.assertEqual(names, ["S38E148.hgt.gz", "S38E149.hgt.gz"])

    def test_empty_when_bounds_degenerate_lon(self) -> None:
        names = iter_skadi_tile_names_for_wgs84_bounds(2.7, -10.5, -3.8, -9.95)
        self.assertEqual(names, [])


if __name__ == "__main__":
    unittest.main()
