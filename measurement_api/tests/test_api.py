import shutil
import tempfile

from django.test import override_settings
from rest_framework.test import APITestCase
from shapely.geometry import LineString, Point, Polygon

from . import helpers as h

TMP_MEDIA = tempfile.mkdtemp(prefix="test_media_")


def tearDownModule():
    shutil.rmtree(TMP_MEDIA, ignore_errors=True)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ApiTestBase(APITestCase):
    def upload(self, f):
        return self.client.post("/api/files/", {"file": f}, format="multipart")

    def measurements(self, file_id, **params):
        r = self.client.get(f"/api/files/{file_id}/measurements/", params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["results"]


class KmlTests(ApiTestBase):
    def test_polygon_line_point_flow(self):
        poly = h.box(80.20, 13.05)  # Chennai
        line = LineString([(80.20, 13.05), (80.25, 13.10), (80.30, 13.10)])
        kml = h.kml_bytes(h.kml_polygon("plot", poly) + h.kml_line("road", line) + h.kml_point("gate", Point(80.2, 13.05)))

        r = self.upload(h.kml_upload(kml))
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["status"], "COMPLETED")
        self.assertEqual(body["feature_count"], 3)
        self.assertEqual(body["crs"], "EPSG:4326")
        self.assertEqual(body["filename"], "survey.kml")

        info = self.client.get(f"/api/files/{body['id']}/").json()
        self.assertEqual(info["id"], body["id"])

        m = {x["geometry_type"]: x for x in self.measurements(body["id"])}
        self.assertEqual(m["Polygon"]["status"], "MEASURED")
        self.assertAlmostEqual(m["Polygon"]["measurements"]["area_sq_m"] / h.geodesic_area(poly), 1, delta=0.001)
        self.assertAlmostEqual(m["LineString"]["measurements"]["length_m"] / h.geodesic_length(line), 1, delta=0.001)
        self.assertEqual(m["Point"]["status"], "NOT_APPLICABLE")
        self.assertEqual(m["Point"]["measurements"], {})

    def test_degrees_are_not_used_directly(self):
        # A 0.01° x 0.01° box is ~1.1 km² at 60°N; degrees² would give 0.0001.
        poly = h.box(10.0, 60.0)
        r = self.upload(h.kml_upload(h.kml_bytes(h.kml_polygon("p", poly))))
        area = self.measurements(r.json()["id"])[0]["measurements"]["area_sq_m"]
        self.assertGreater(area, 500_000)
        self.assertAlmostEqual(area / h.geodesic_area(poly), 1, delta=0.001)

    def test_features_endpoint_returns_geometry_and_properties(self):
        r = self.upload(h.kml_upload(h.kml_bytes(h.kml_polygon("plot", h.box(0, 0)))))
        feats = self.client.get(f"/api/files/{r.json()['id']}/features/").json()["results"]
        self.assertEqual(feats[0]["feature_id"], 0)
        self.assertEqual(feats[0]["geometry"]["type"], "Polygon")
        self.assertEqual(feats[0]["crs"], "EPSG:4326")
        self.assertEqual(feats[0]["properties"]["Name"], "plot")
        self.assertEqual(feats[0]["properties"]["owner"], "acme")

    def test_geometry_collection_is_unsupported_not_a_crash(self):
        multi = """<Placemark><name>mix</name><MultiGeometry>
          <Point><coordinates>1,1,0</coordinates></Point>
          <LineString><coordinates>1,1,0 2,2,0</coordinates></LineString></MultiGeometry></Placemark>"""
        r = self.upload(h.kml_upload(h.kml_bytes(multi + h.kml_polygon("ok", h.box(1, 1)))))
        self.assertEqual(r.status_code, 201, r.content)
        by_type = {x["geometry_type"]: x for x in self.measurements(r.json()["id"])}
        self.assertEqual(by_type["GeometryCollection"]["status"], "UNSUPPORTED")
        self.assertTrue(by_type["GeometryCollection"]["note"])
        self.assertEqual(by_type["Polygon"]["status"], "MEASURED")

    def test_self_intersecting_polygon_is_repaired_and_flagged(self):
        bowtie = "0,0,0 0.01,0.01,0 0.01,0,0 0,0.01,0 0,0,0"
        kml = h.kml_bytes(
            f"<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>{bowtie}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )
        m = self.measurements(self.upload(h.kml_upload(kml)).json()["id"])[0]
        self.assertEqual(m["status"], "MEASURED")
        self.assertIn("repaired", m["note"])
        self.assertGreater(m["measurements"]["area_sq_m"], 0)

    def test_multiple_folders_become_layers_with_global_indexes(self):
        kml = f"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
        <Folder><name>A</name>{h.kml_polygon('a', h.box(0, 0))}</Folder>
        <Folder><name>B</name>{h.kml_polygon('b', h.box(1, 1))}</Folder></Document></kml>""".encode()
        r = self.upload(h.kml_upload(kml))
        results = self.measurements(r.json()["id"])
        self.assertEqual([x["feature_id"] for x in results], [0, 1])
        self.assertEqual({x["layer"] for x in results}, {"A", "B"})
        filtered = self.measurements(r.json()["id"], layer="B")
        self.assertEqual(len(filtered), 1)

    def test_invalid_kml_is_failed_with_422(self):
        r = self.upload(h.kml_upload(b"this is not xml"))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["status"], "FAILED")
        self.assertTrue(r.json()["error"])
        # The failed record is still retrievable, but has no measurements.
        fid = r.json()["id"]
        self.assertEqual(self.client.get(f"/api/files/{fid}/").json()["status"], "FAILED")
        self.assertEqual(self.client.get(f"/api/files/{fid}/measurements/").status_code, 409)


class ShapefileTests(ApiTestBase):
    def test_utm_shapefile(self):
        poly = h.box(80.20, 13.05)
        from pyproj import Transformer

        t = Transformer.from_crs(4326, 32644, always_xy=True)
        projected = Polygon([t.transform(x, y) for x, y in poly.exterior.coords])
        r = self.upload(h.zip_upload(h.shapefile_zip([projected], "EPSG:32644")))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["crs"], "EPSG:32644")
        m = self.measurements(r.json()["id"])[0]
        self.assertAlmostEqual(m["measurements"]["area_sq_m"] / h.geodesic_area(poly), 1, delta=0.001)

    def test_web_mercator_input_is_not_measured_in_mercator_units(self):
        # Web Mercator inflates area by 1/cos²(lat) (~4x at 60°N) if used naively.
        from pyproj import Transformer

        poly = h.box(10.0, 60.0)
        t = Transformer.from_crs(4326, 3857, always_xy=True)
        merc = Polygon([t.transform(x, y) for x, y in poly.exterior.coords])
        self.assertGreater(merc.area / h.geodesic_area(poly), 3.5)  # sanity: naive value is badly wrong
        r = self.upload(h.zip_upload(h.shapefile_zip([merc], "EPSG:3857")))
        m = self.measurements(r.json()["id"])[0]
        self.assertAlmostEqual(m["measurements"]["area_sq_m"] / h.geodesic_area(poly), 1, delta=0.001)

    def test_non_metre_units_us_survey_feet(self):
        from pyproj import Transformer

        line = LineString([(-74.0, 40.7), (-73.9, 40.75)])
        t = Transformer.from_crs(4326, 2263, always_xy=True)  # NY Long Island, US survey feet
        ft_line = LineString([t.transform(x, y) for x, y in line.coords])
        r = self.upload(h.zip_upload(h.shapefile_zip([ft_line], "EPSG:2263")))
        m = self.measurements(r.json()["id"])[0]
        self.assertAlmostEqual(m["measurements"]["length_m"] / h.geodesic_length(line), 1, delta=0.001)

    def test_geographic_non_wgs84_and_attributes(self):
        poly = h.box(-100.0, 40.0)
        r = self.upload(h.zip_upload(h.shapefile_zip([poly], "EPSG:4269", props={"parcel": ["P-1"], "value": [12.5]})))
        self.assertEqual(r.json()["crs"], "EPSG:4269")
        fid = r.json()["id"]
        feat = self.client.get(f"/api/files/{fid}/features/").json()["results"][0]
        self.assertEqual(feat["properties"], {"parcel": "P-1", "value": 12.5})
        m = self.measurements(fid)[0]
        self.assertAlmostEqual(m["measurements"]["area_sq_m"] / h.geodesic_area(poly), 1, delta=0.002)

    def test_points_and_mixed_batch(self):
        r = self.upload(h.zip_upload(h.shapefile_zip([Point(1, 1), Point(2, 2)], "EPSG:4326")))
        res = self.measurements(r.json()["id"])
        self.assertEqual({x["status"] for x in res}, {"NOT_APPLICABLE"})

    def test_missing_prj_is_rejected_with_clear_message(self):
        r = self.upload(h.zip_upload(h.shapefile_zip([h.box(0, 0)], "EPSG:4326", with_prj=False)))
        self.assertEqual(r.status_code, 422)
        self.assertIn(".prj", r.json()["error"])

    def test_zip_without_shp(self):
        r = self.upload(h.zip_upload(h.raw_zip({"readme.txt": b"hi"})))
        self.assertEqual(r.status_code, 422)
        self.assertIn(".shp", r.json()["error"])

    def test_corrupt_zip(self):
        r = self.upload(h.zip_upload(b"PK not really a zip"))
        self.assertEqual(r.status_code, 422)
        self.assertIn("zip", r.json()["error"].lower())

    def test_zip_slip_rejected(self):
        r = self.upload(h.zip_upload(h.raw_zip({"../../evil.shp": b"x"})))
        self.assertEqual(r.status_code, 422)
        self.assertIn("unsafe", r.json()["error"])

    @override_settings(MAX_UNZIPPED_BYTES=1000)
    def test_zip_bomb_rejected(self):
        r = self.upload(h.zip_upload(h.raw_zip({"a.shp": b"0" * 100_000})))
        self.assertEqual(r.status_code, 422)
        self.assertIn("too large", r.json()["error"])


class ValidationAndPaginationTests(ApiTestBase):
    def test_unsupported_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        r = self.upload(SimpleUploadedFile("a.geojson", b"{}"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("file", r.json())

    def test_missing_file_field(self):
        self.assertEqual(self.client.post("/api/files/", {}, format="multipart").status_code, 400)

    def test_empty_file(self):
        self.assertEqual(self.upload(h.kml_upload(b"")).status_code, 400)

    @override_settings(MAX_UPLOAD_BYTES=10)
    def test_too_large(self):
        self.assertEqual(self.upload(h.kml_upload(b"x" * 100)).status_code, 400)

    def test_unknown_id_404(self):
        self.assertEqual(self.client.get("/api/files/00000000-0000-0000-0000-000000000000/").status_code, 404)
        self.assertEqual(self.client.get("/api/files/not-a-uuid/measurements/").status_code, 404)

    def test_pagination(self):
        pts = [Point(i, i) for i in range(25)]
        fid = self.upload(h.zip_upload(h.shapefile_zip(pts, "EPSG:4326"))).json()["id"]
        r = self.client.get(f"/api/files/{fid}/measurements/", {"limit": 10, "offset": 20}).json()
        self.assertEqual(r["count"], 25)
        self.assertEqual([x["feature_id"] for x in r["results"]], [20, 21, 22, 23, 24])
