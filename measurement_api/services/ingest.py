"""Reading Shapefile (.zip) and KML uploads into in-memory layers."""
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pyogrio
from django.conf import settings
from pyproj import CRS

from .crs import WGS84


class IngestError(Exception):
    """The upload cannot be read; message is safe to show to the API client."""


@dataclass
class Layer:
    name: str
    crs: CRS
    frame: gpd.GeoDataFrame


def parse(path: Path, file_type: str) -> list[Layer]:
    if file_type == "SHAPEFILE":
        return _parse_shapefile_zip(path)
    if file_type == "KML":
        return _parse_kml(path)
    raise IngestError(f"Unsupported file type: {file_type}")


# --- Shapefile ---------------------------------------------------------------
def _parse_shapefile_zip(zip_path: Path) -> list[Layer]:
    workdir = Path(tempfile.mkdtemp(prefix="shp_"))
    try:
        _safe_extract(zip_path, workdir)
        shp_files = sorted(
            p for p in workdir.rglob("*") if p.suffix.lower() == ".shp" and "__MACOSX" not in p.parts
        )
        if not shp_files:
            raise IngestError("The zip archive does not contain a .shp file.")

        layers = []
        for shp in shp_files:
            try:
                frame = gpd.read_file(shp)
            except Exception as exc:
                raise IngestError(
                    f"Could not read '{shp.name}'. Make sure the .shx and .dbf files are included. ({_short(exc)})"
                ) from exc
            if frame.crs is None:
                raise IngestError(
                    f"'{shp.name}' has no CRS (missing .prj file). Include the .prj so measurements can be projected correctly."
                )
            layers.append(Layer(shp.stem, CRS.from_user_input(frame.crs), frame))
        return layers
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # features are already in memory


def _safe_extract(zip_path: Path, dest: Path) -> None:
    """Extract with zip-slip, zip-bomb and entry-count protection."""
    dest = dest.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > settings.MAX_ZIP_ENTRIES:
                raise IngestError(f"Zip contains too many entries (limit {settings.MAX_ZIP_ENTRIES}).")
            if sum(i.file_size for i in infos) > settings.MAX_UNZIPPED_BYTES:
                raise IngestError("Zip contents are too large when uncompressed.")

            written = 0
            for info in infos:
                if info.is_dir():
                    continue
                target = (dest / info.filename).resolve()
                if not target.is_relative_to(dest):
                    raise IngestError("Zip contains an unsafe file path.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    # Headers can lie about size, so enforce the cap while copying.
                    while chunk := src.read(1024 * 1024):
                        written += len(chunk)
                        if written > settings.MAX_UNZIPPED_BYTES:
                            raise IngestError("Zip contents are too large when uncompressed.")
                        out.write(chunk)
    except zipfile.BadZipFile as exc:
        raise IngestError("The uploaded file is not a valid zip archive.") from exc
    except RuntimeError as exc:  # password-protected archives
        raise IngestError("The zip archive is encrypted or unreadable.") from exc


# --- KML ---------------------------------------------------------------------
def _parse_kml(path: Path) -> list[Layer]:
    try:
        layer_names = [row[0] for row in pyogrio.list_layers(path)]
    except Exception as exc:
        raise IngestError(f"The file is not valid KML. ({_short(exc)})") from exc

    layers = []
    for name in layer_names:  # one OGR layer per KML <Folder>/<Document>
        try:
            frame = gpd.read_file(path, layer=name)
        except Exception as exc:
            raise IngestError(f"Could not read KML layer '{name}'. ({_short(exc)})") from exc
        if frame.empty:
            continue
        # The KML spec mandates WGS84 lon/lat, so a missing CRS is safe to default.
        layers.append(Layer(name, CRS.from_user_input(frame.crs or WGS84), frame))
    return layers


def _short(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:200] if str(exc).strip() else exc.__class__.__name__
