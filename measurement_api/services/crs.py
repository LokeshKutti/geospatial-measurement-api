"""CRS helpers: labelling, transformer caching and projected-CRS selection.

Strategy (see README "CRS handling"):

* Every geometry is first brought to EPSG:4326 (lon/lat), whatever it arrived in.
* AREA is measured in a *local Lambert Azimuthal Equal-Area* projection centred
  on the geometry. Equal-area is exact for area by construction and, unlike UTM,
  has no zone boundaries, so features that straddle zones are still correct.
* LENGTH is measured in the *UTM zone* of the geometry's centre (UPS near the
  poles). UTM is conformal with <0.1% scale error inside a zone, and its
  scale error is the property that matters for distances.
"""
from functools import lru_cache

from pyproj import CRS, Transformer

WGS84 = CRS.from_epsg(4326)

_UPS_NORTH = 32661  # WGS 84 / UPS North (N,E)
_UPS_SOUTH = 32761  # WGS 84 / UPS South (N,E)


def crs_label(crs: CRS) -> str:
    """'EPSG:4326' when the CRS can be identified, otherwise its name."""
    authority = crs.to_authority(min_confidence=70)
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.name or "UNKNOWN"


@lru_cache(maxsize=512)
def _transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True)


def get_transformer(src: CRS, dst: CRS) -> Transformer:
    # CRS objects are hashed via WKT; strings keep the lru_cache key cheap/stable.
    return _transformer(src.to_wkt(), dst.to_wkt())


def utm_crs_for(lon: float, lat: float) -> CRS:
    """UTM zone covering (lon, lat); UPS beyond 84°N / 80°S where UTM is undefined."""
    if lat >= 84:
        return CRS.from_epsg(_UPS_NORTH)
    if lat <= -80:
        return CRS.from_epsg(_UPS_SOUTH)
    zone = min(60, max(1, int((lon + 180) // 6) + 1))
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def equal_area_crs_for(lon: float, lat: float) -> CRS:
    """Local Lambert Azimuthal Equal-Area CRS centred on (lon, lat), metres."""
    return CRS.from_proj4(
        f"+proj=laea +lat_0={lat:.6f} +lon_0={lon:.6f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
