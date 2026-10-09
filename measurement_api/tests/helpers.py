"""Builders for in-memory test fixtures (no binary files checked in)."""
import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from django.core.files.uploadedfile import SimpleUploadedFile
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon

GEOD = Geod(ellps="WGS84")


def box(lon, lat, size=0.01):
    return Polygon([(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size)])


def geodesic_area(poly) -> float:
    return abs(GEOD.geometry_area_perimeter(poly)[0])


def geodesic_length(line) -> float:
    return GEOD.geometry_length(line)


def kml_bytes(placemarks: str, folder: str = "Test") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>{folder}</name>
{placemarks}
</Folder></Document></kml>""".encode()


def kml_polygon(name, poly: Polygon) -> str:
    coords = " ".join(f"{x},{y},0" for x, y in poly.exterior.coords)
    return f"""<Placemark><name>{name}</name><ExtendedData><Data name="owner"><value>acme</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"""


def kml_line(name, line: LineString) -> str:
    coords = " ".join(f"{x},{y},0" for x, y in line.coords)
    return f"<Placemark><name>{name}</name><LineString><coordinates>{coords}</coordinates></LineString></Placemark>"


def kml_point(name, pt: Point) -> str:
    return f"<Placemark><name>{name}</name><Point><coordinates>{pt.x},{pt.y},0</coordinates></Point></Placemark>"


def kml_upload(content: bytes, name="survey.kml"):
    return SimpleUploadedFile(name, content, content_type="application/vnd.google-earth.kml+xml")


def shapefile_zip(geoms, crs: str, name="parcels", props=None, with_prj=True) -> bytes:
    """Write a GeoDataFrame as a Shapefile and zip all sidecar files."""
    gdf = gpd.GeoDataFrame(props or {"id": list(range(len(geoms)))}, geometry=geoms, crs=crs)
    with tempfile.TemporaryDirectory() as tmp:
        gdf.to_file(Path(tmp) / f"{name}.shp")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for f in sorted(Path(tmp).iterdir()):
                if not with_prj and f.suffix == ".prj":
                    continue
                zf.write(f, f.name)
        return buf.getvalue()


def zip_upload(content: bytes, name="data.zip"):
    return SimpleUploadedFile(name, content, content_type="application/zip")


def raw_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, data in entries.items():
            zf.writestr(n, data)
    return buf.getvalue()
