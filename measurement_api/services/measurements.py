"""Measurement calculation for a single geometry."""
from dataclasses import dataclass, field

import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from .crs import WGS84, crs_label, equal_area_crs_for, get_transformer, utm_crs_for

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
NON_MEASURABLE = {"Point", "MultiPoint"}


@dataclass
class MeasurementResult:
    status: str  # MEASURED | NOT_APPLICABLE | UNSUPPORTED | ERROR
    values: dict = field(default_factory=dict)
    crs: str = ""  # projected CRS used for the calculation
    note: str = ""


def _project(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    projected = shapely_transform(get_transformer(src, dst).transform, geom)
    minx, miny, maxx, maxy = projected.bounds
    if not all(map(_finite, (minx, miny, maxx, maxy))):
        raise ValueError(f"Coordinates could not be transformed to {crs_label(dst)}")
    return projected


def _finite(v: float) -> bool:
    return v == v and v not in (float("inf"), float("-inf"))


def _to_wgs84(geom: BaseGeometry, source_crs: CRS) -> BaseGeometry:
    return geom if source_crs == WGS84 else _project(geom, source_crs, WGS84)


def _center(geom_wgs84: BaseGeometry) -> tuple[float, float]:
    minx, miny, maxx, maxy = geom_wgs84.bounds
    return (minx + maxx) / 2, (miny + maxy) / 2


def _repair_polygonal(geom: BaseGeometry) -> BaseGeometry:
    """make_valid can return a GeometryCollection; keep only the polygonal parts."""
    fixed = shapely.make_valid(geom)
    if fixed.geom_type in POLYGONAL:
        return fixed
    parts = [g for g in getattr(fixed, "geoms", []) if g.geom_type in POLYGONAL]
    return shapely.union_all(parts) if parts else fixed


def measure(geom: BaseGeometry | None, source_crs: CRS) -> MeasurementResult:
    """Never raises: failures are reported through `status`/`note`."""
    if geom is None or geom.is_empty:
        return MeasurementResult("UNSUPPORTED", note="Geometry is missing or empty.")

    gtype = geom.geom_type
    if gtype in NON_MEASURABLE:
        return MeasurementResult("NOT_APPLICABLE", note=f"{gtype} has no area or length.")
    if gtype not in POLYGONAL | LINEAR:
        return MeasurementResult("UNSUPPORTED", note=f"Measurement is not supported for {gtype}.")

    try:
        geom = shapely.force_2d(geom)  # KML carries altitude; it must not affect planar maths
        note = ""
        if gtype in POLYGONAL and not geom.is_valid:
            geom = _repair_polygonal(geom)
            note = "Invalid polygon (e.g. self-intersection) was repaired before measuring."

        wgs84 = _to_wgs84(geom, source_crs)
        lon, lat = _center(wgs84)

        if gtype in POLYGONAL:
            target = equal_area_crs_for(lon, lat)
            target_label = f"Local LAEA (lon_0={lon:.4f}, lat_0={lat:.4f})"
            area = _project(wgs84, WGS84, target).area
            values = {
                "area_sq_m": round(area, 4),
                "area_sq_km": round(area / 1e6, 6),
                "area_hectares": round(area / 1e4, 4),
            }
        else:
            target = utm_crs_for(lon, lat)
            target_label = crs_label(target)
            length = _project(wgs84, WGS84, target).length
            values = {"length_m": round(length, 4), "length_km": round(length / 1e3, 6)}

        return MeasurementResult("MEASURED", values, target_label, note)
    except Exception as exc:  # one bad feature must not fail the whole file
        return MeasurementResult("ERROR", note=f"Measurement failed: {exc}")
