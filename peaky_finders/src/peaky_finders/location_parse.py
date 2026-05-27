"""Parse --location strings: decimal pair or degrees with hemisphere and optional altitude."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DECIMAL_PAIR = re.compile(
    r"^\s*(?P<lat>[+-]?\d+(?:\.\d+)?)\s*,\s*(?P<lon>[+-]?\d+(?:\.\d+)?)\s*$"
)

# e.g. 39.996667 N 119.235556 W 6535 ft
_VERBOSE = re.compile(
    r"""
    ^\s*
    (?P<lat>[+-]?\d+(?:\.\d+)?)\s*(?P<lah>[NnSs])\s+
    (?P<lon>[+-]?\d+(?:\.\d+)?)\s*(?P<loh>[EeWw])
    (?:\s+(?P<alt>[+-]?\d+(?:\.\d+)?)\s*(?P<altu>ft|feet|'|m|meter|meters)\b)?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedLocation:
    lat: float
    lon: float
    altitude_label: str | None = None


def _signed_lat_lon(lat_deg: float, lah: str, lon_deg: float, loh: str) -> tuple[float, float]:
    lah_u = lah.upper()
    loh_u = loh.upper()
    lat_mag = abs(float(lat_deg))
    lon_mag = abs(float(lon_deg))
    lat = lat_mag if lah_u == "N" else -lat_mag
    lon = lon_mag if loh_u == "E" else -lon_mag
    return lat, lon


def _normalize_alt_label(value: str, unit: str) -> str:
    unit_l = unit.lower().rstrip("s")
    v = value.strip()
    if unit_l in ("ft", "feet", "'"):
        return f"{v} ft"
    return f"{v} m"


def parse_location(s: str) -> ParsedLocation:
    text = (s or "").strip()
    if not text:
        raise ValueError("location must not be empty")

    normalized = " ".join(text.replace("\t", " ").split())
    m_verbose = _VERBOSE.match(normalized)
    if m_verbose:
        gd = m_verbose.groupdict()
        lat, lon = _signed_lat_lon(float(gd["lat"]), gd["lah"], float(gd["lon"]), gd["loh"])
        alt = gd.get("alt")
        altu = gd.get("altu")
        alt_label = _normalize_alt_label(alt, altu) if alt and altu else None
        return ParsedLocation(lat=lat, lon=lon, altitude_label=alt_label)

    m_pair = _DECIMAL_PAIR.match(normalized)
    if m_pair:
        return ParsedLocation(
            lat=float(m_pair.group("lat")),
            lon=float(m_pair.group("lon")),
            altitude_label=None,
        )

    raise ValueError(
        'location must be "lat,lon" (decimals) or '
        '"<lat>N|S <lon>E|W [<alt> ft|m]" e.g. '
        '"39.996667 N 119.235556 W 6535 ft"'
    )
