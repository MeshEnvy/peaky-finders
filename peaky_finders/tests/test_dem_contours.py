"""DEM contour generation for web map."""

from __future__ import annotations

import numpy as np

from peaky_finders.web.dem_contours import _contour_levels


def test_contour_levels_interval() -> None:
    elev = np.array([[100.0, 150.0], [200.0, 250.0]])
    levels = _contour_levels(elev, interval_m=50.0)
    assert 150.0 in levels
    assert 200.0 in levels
