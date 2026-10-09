# Geospatial File Measurement API

A Django + Django REST Framework service that accepts a **Shapefile (.zip)** or **KML**, extracts every feature
(ID, geometry type, geometry, CRS, attributes) and returns **CRS-correct measurements**: area for polygons, length
for lines. Points are returned with no measurement; unsupported geometries are reported, never crash the request.

## Setup

Requires Python 3.10+. No system GDAL is needed: `pyogrio`, `pyproj` and `shapely` ship wheels that bundle GDAL/PROJ/GEOS.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver            # http://127.0.0.1:8000
```

Try it with the bundled samples (`python scripts/make_samples.py` regenerates them):

```bash
curl -F file=@samples/survey.kml         http://127.0.0.1:8000/api/files/
curl -F file=@samples/parcels_utm44n.zip http://127.0.0.1:8000/api/files/
```

Interactive docs (Swagger UI): <http://127.0.0.1:8000/api/docs/> · OpenAPI schema: `/api/schema/`

**Tests** (23 tests, ~0.5 s): `python manage.py test`

**Docker**: `docker build -t geo-api . && docker run -p 8000:8000 -v geo-data:/data geo-api`

**Configuration** (env vars): `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (`1`/`0`), `DJANGO_ALLOWED_HOSTS`, `DJANGO_DB_PATH`,
`DJANGO_MEDIA_ROOT`, `MAX_UPLOAD_BYTES` (default 50 MB), `MAX_UNZIPPED_BYTES` (default 500 MB), `MAX_ZIP_ENTRIES` (default 1000).

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/files/` | Upload and process a `.zip` (Shapefile) or `.kml` (multipart field `file`) |
| `GET` | `/api/files/{id}/` | File info and processing status |
| `GET` | `/api/files/{id}/measurements/` | Per-feature measurements |
| `GET` | `/api/files/{id}/features/` | Per-feature geometry (GeoJSON, source CRS) and attributes |
| `GET` | `/api/files/` | List uploads |

List endpoints are paginated (`?limit=&offset=`, default 100). `measurements/` and `features/` accept `?layer=<name>`.

### `POST /api/files/`

```bash
curl -F file=@samples/survey.kml http://127.0.0.1:8000/api/files/
```
```json
{
  "id": "a2ddf774-64a9-4632-ba1e-84d9c7d0e796",
  "filename": "survey.kml",
  "file_type": "KML",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error": "",
  "created_at": "2026-10-08T18:00:32.658892Z"
}
```

| Code | When |
|---|---|
| `201` | Processed (`status: COMPLETED`) |
| `400` | Invalid upload: missing/empty file, wrong extension, over the size limit |
| `422` | Valid upload but unreadable as geodata (corrupt zip, no `.shp`, missing `.prj`, invalid KML). The record is kept with `status: FAILED` and an `error` message |

### `GET /api/files/{id}/`

Same shape as the upload response. `status` is one of `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`.
`crs` is `MIXED` if layers in one file use different CRSs (each feature still carries its own).

### `GET /api/files/{id}/measurements/`

```json
{
  "count": 4, "next": null, "previous": null,
  "results": [
    {
      "feature_id": 0, "layer": "Survey", "geometry_type": "Polygon", "crs": "EPSG:4326",
      "status": "MEASURED",
      "measurements": { "area_sq_m": 1199912.3687, "area_sq_km": 1.199912, "area_hectares": 119.9912 },
      "projected_crs": "Local LAEA (lon_0=80.2050, lat_0=13.0550)",
      "note": ""
    },
    {
      "feature_id": 2, "layer": "Survey", "geometry_type": "LineString", "crs": "EPSG:4326",
      "status": "MEASURED",
      "measurements": { "length_m": 4406.549, "length_km": 4.406549 },
      "projected_crs": "EPSG:32644", "note": ""
    },
    {
      "feature_id": 3, "layer": "Survey", "geometry_type": "Point", "crs": "EPSG:4326",
      "status": "NOT_APPLICABLE", "measurements": {}, "projected_crs": "",
      "note": "Point has no area or length."
    }
  ]
}
```

Per-feature `status`:

| Status | Meaning |
|---|---|
| `MEASURED` | Area (polygons) or length (lines) calculated. `note` may say the polygon was repaired |
| `NOT_APPLICABLE` | `Point` / `MultiPoint` |
| `UNSUPPORTED` | `GeometryCollection`, or null/empty geometry. Explained in `note` |
| `ERROR` | Unexpected failure on this feature only. The rest of the file is unaffected |

Calling this (or `features/`) on a file that is not `COMPLETED` returns `409` with the reason.

### `GET /api/files/{id}/features/`

```json
{ "feature_id": 0, "layer": "Survey", "geometry_type": "Polygon",
  "geometry": { "type": "Polygon", "coordinates": [[[80.2, 13.05, 0.0], "..."]] },
  "crs": "EPSG:4326", "properties": { "Name": "Parcel A", "owner": "Asha" } }
```

## Architecture

```
config/                  Django project (settings, urls)
measurement_api/
  models.py              UploadedFile, Feature
  serializers.py         Upload validation, response shapes
  views.py               FileViewSet (create / retrieve / list / measurements / features)
  services/
    ingest.py            Safe zip extraction; Shapefile/KML -> GeoDataFrames
    crs.py               CRS labelling, transformer cache, projected-CRS selection
    measurements.py      measure(geometry, source_crs) -> MeasurementResult
    processing.py        Orchestrator: parse -> measure -> persist
  tests/
```

Views are thin; all geospatial logic lives in `services/` as plain Python with no Django/HTTP dependencies, so it
is unit-testable and reusable (e.g. from a background worker).

### File-processing flow

1. `POST /api/files/`: the serializer validates extension (`.zip`/`.kml`), non-empty, and size; the file is saved and an `UploadedFile` row is created (`PENDING`).
2. `processing.process_uploaded_file` sets `PROCESSING` and calls `ingest.parse`:
   * **Shapefile**: the zip is extracted to a temp dir with zip-slip, entry-count and uncompressed-size (zip-bomb) guards; every `.shp` inside is read with GeoPandas/pyogrio. A missing `.prj` is rejected.
   * **KML**: each `<Folder>`/`<Document>` becomes a layer (OGR's model) and is read in turn. A missing CRS defaults to WGS84, as the KML spec requires.
3. Each feature gets a global 0-based `feature_id` (stable across layers), its geometry as GeoJSON in the **source CRS**, its CRS label, and its attributes, made JSON-safe (NaN/NaT/numpy/datetime).
4. Each geometry is measured (below). All features are saved with `bulk_create` inside one transaction, then the file becomes `COMPLETED`.
5. Any `IngestError` (bad file) or unexpected exception yields `FAILED` with a client-safe message. The temp dir is always removed.

### Measurement calculation flow

`measure(geom, source_crs)` never raises; it returns a status:

1. Null/empty geometry -> `UNSUPPORTED`. Point/MultiPoint -> `NOT_APPLICABLE`. Anything other than (Multi)Polygon / (Multi)LineString -> `UNSUPPORTED`.
2. Drop Z (KML carries altitude, which must not affect planar maths).
3. Invalid polygons (e.g. a bow-tie) are repaired with `make_valid` (polygonal parts kept) and flagged in `note`.
4. Transform to EPSG:4326, pick a local projected CRS from the geometry's centre, project, measure with Shapely, round.
5. `MultiPolygon`/`MultiLineString` are summed; polygon holes are subtracted.

### CRS handling

Lat/lon degrees are **never** used for area or distance. Whatever the input CRS (4326, 4269, UTM, Web Mercator, US survey feet...), the geometry
is first converted to WGS84, then projected into a CRS chosen *per feature*:

| Measurement | Projected CRS | Why |
|---|---|---|
| **Area** | Local **Lambert Azimuthal Equal-Area** centred on the feature | Equal-area by construction, so area is exact regardless of size/latitude; no zone boundaries to straddle |
| **Length** | **UTM zone** of the feature's centre (UPS beyond 84°N / 80°S) | Conformal with <0.1% scale error within a zone: the property that matters for distances |

Input in a projected CRS is *not* measured in its own units or projection. Web Mercator inflates area ~4x at 60°N and
state-plane feet aren't metres, so everything goes through WGS84 -> local CRS. Tests assert results agree with
`pyproj.Geod` ellipsoidal calculations to within 0.1% for 4326, 4269, UTM, Web Mercator and US-survey-foot inputs.

Pyproj `Transformer`s are cached (`always_xy=True`, so axis order is always lon/lat).

## Design decisions

* **DRF + plain `services/` instead of GeoDjango/PostGIS.** GeoDjango needs system GDAL/GEOS and a spatial DB, which makes local setup the main hurdle. The requirements are file-in/measurements-out; no spatial queries. GeoPandas/pyogrio wheels bundle GDAL so `pip install` is enough. Trade-off: no spatial indexing/queries. If those were needed I would move `Feature.geometry` to a PostGIS `GeometryField`.
* **Two CRS strategies (LAEA for area, UTM for length) vs. one.** A single UTM zone for everything is simpler but gives area error for features spanning zones; a pure geodesic (`pyproj.Geod`) is the most accurate but the brief asks for projected measurement. Equal-area for area and conformal for length picks the right invariant for each. Geodesic is used in tests as the independent oracle.
* **Synchronous processing.** The brief's example response returns `status: COMPLETED`, and 50 MB files parse in seconds. The status field, `PENDING/PROCESSING` states and the service-layer split are already in place to move `process_uploaded_file` to Celery/RQ with no API change (return `202`, client polls `GET /files/{id}/`).
* **Features persisted in the DB (JSON columns), measured once at upload.** Reading measurements is then a cheap paginated query rather than a re-parse of the file. Alternative: store only the file and compute on demand; simpler storage but repeated CPU work and no stable `feature_id`.
* **Per-feature fault isolation.** Unsupported/invalid/failed features get a `status` and `note`, not a failed request, as the brief requires. Invalid polygons are repaired rather than rejected, with a visible note.
* **Missing `.prj` is a hard error, not an assumption.** Guessing WGS84 for projected coordinates would produce silently wrong numbers; a clear 422 is safer. KML is the exception, since the spec fixes its CRS.
* **`422` with a stored `FAILED` record** (rather than only an error body) so clients can still `GET` the failure later and the failure is auditable.
* **Upload hardening:** extension/size/empty checks, zip-slip protection, entry-count cap, uncompressed-size cap enforced *while copying* (zip headers can lie), temp dir cleanup, no archive contents are ever executed.
* **Always metric output:** measurements are returned in metres/km/hectares regardless of source units.
* **No auth/rate limiting.** Out of scope for the exercise; the DRF permission/throttle hooks are where they would go.

## Known limitations

* Polygon/line edges are straight lines in the *projected* CRS between vertices; very long edges (hundreds of km) differ from true geodesics. Densifying segments before projecting would fix it.
* Geometries crossing the antimeridian (±180°) are not specially handled.
* KMZ, GeoJSON, GPKG are not accepted (only `.zip` Shapefile and `.kml`).
* SQLite is used for convenience; multi-million-feature files would warrant PostGIS and async ingestion.

## Learnings

* The easiest geospatial bug to write is the silent one: Web Mercator and feet inputs return *plausible-looking* numbers. Cross-checking against an ellipsoidal oracle (`pyproj.Geod`) in tests catches the class of mistakes that eyeballing can't.
* Equal-area vs conformal is not a style choice: picking the projection by *which quantity you measure* removes most accuracy worries.
* KML is not "one layer": Folders map to OGR layers, so a global feature index and a `layer` field are needed. KML also carries altitude, and GeometryCollections arise easily from `<MultiGeometry>`.
* Real-world files are messy (NaN/NaT attributes, bow-tie polygons, no `.prj`, malicious zips), so most of the code is validation and graceful degradation rather than the measurement maths.
* Keeping geospatial logic out of views made it trivially testable with in-memory fixtures (no binary files in the repo).

## Future scope

* Async processing (Celery/RQ) with `202 Accepted`, progress and webhooks; chunked/streaming ingestion for very large files.
* PostGIS storage for spatial queries (bbox filter, intersects, nearest) and vector tiles.
* More formats (KMZ, GeoJSON, GeoPackage, File GDB) and more measures (polygon perimeter, line sinuosity, centroid, bounding box).
* Segment densification and antimeridian handling for geodesically exact results.
* Optional user-chosen CRS/units on the measurement endpoint (`?crs=EPSG:xxxx&units=ft`) and CSV/GeoJSON export of results.
* Authentication, per-user quotas, rate limiting, object storage (S3) for uploads, CI pipeline and tracing.
