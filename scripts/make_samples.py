"""Generate sample upload files into ./samples (Chennai-area parcels and a road)."""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

OUT = Path(__file__).resolve().parent.parent / "samples"
OUT.mkdir(exist_ok=True)

parcel_a = Polygon([(80.200, 13.050), (80.210, 13.050), (80.210, 13.060), (80.200, 13.060)])
parcel_b = Polygon([(80.220, 13.050), (80.230, 13.050), (80.225, 13.062)])
road = LineString([(80.200, 13.045), (80.215, 13.048), (80.240, 13.052)])
gate = Point(80.205, 13.055)


def placemark(name, geom_xml):
    return f"<Placemark><name>{name}</name>{geom_xml}</Placemark>"


def ring(poly):
    return " ".join(f"{x},{y},0" for x, y in poly.exterior.coords)


kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>Survey</name>
{placemark("Parcel A", f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(parcel_a)}</coordinates></LinearRing></outerBoundaryIs></Polygon>")}
{placemark("Parcel B", f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(parcel_b)}</coordinates></LinearRing></outerBoundaryIs></Polygon>")}
{placemark("Access road", f"<LineString><coordinates>{' '.join(f'{x},{y},0' for x, y in road.coords)}</coordinates></LineString>")}
{placemark("Gate", f"<Point><coordinates>{gate.x},{gate.y},0</coordinates></Point>")}
</Folder></Document></kml>"""
(OUT / "survey.kml").write_text(kml)

# Same parcels as a Shapefile, deliberately in a projected CRS (UTM 44N) to exercise reprojection.
gdf = gpd.GeoDataFrame(
    {"name": ["Parcel A", "Parcel B"], "owner": ["Asha", "Ravi"]}, geometry=[parcel_a, parcel_b], crs="EPSG:4326"
).to_crs("EPSG:32644")
with tempfile.TemporaryDirectory() as tmp:
    gdf.to_file(Path(tmp) / "parcels.shp")
    with zipfile.ZipFile(OUT / "parcels_utm44n.zip", "w") as zf:
        for f in sorted(Path(tmp).iterdir()):
            zf.write(f, f.name)
print("Wrote", *sorted(p.name for p in OUT.iterdir()), sep="\n  ")
