"""Orchestrates parse -> measure -> persist for an UploadedFile."""
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd
from django.db import transaction
from shapely.geometry import mapping

from ..models import Feature, UploadedFile
from . import ingest
from .crs import crs_label
from .measurements import measure

logger = logging.getLogger(__name__)
BATCH_SIZE = 1000


def process_uploaded_file(upload: UploadedFile) -> UploadedFile:
    """Parse the stored file and persist its features with measurements.

    Always leaves `upload` in COMPLETED or FAILED; never raises.
    """
    upload.status = UploadedFile.Status.PROCESSING
    upload.save(update_fields=["status"])

    try:
        layers = ingest.parse(_path(upload), upload.file_type)
        features, crs_labels = [], set()
        index = 0
        for layer in layers:
            layer_crs = crs_label(layer.crs)
            crs_labels.add(layer_crs)
            props_frame = layer.frame.drop(columns=layer.frame.geometry.name)
            for geom, props in zip(layer.frame.geometry, props_frame.to_dict("records")):
                result = measure(geom, layer.crs)
                features.append(
                    Feature(
                        file=upload,
                        index=index,
                        layer=layer.name,
                        geometry_type=geom.geom_type if geom is not None else "None",
                        geometry=mapping(geom) if geom is not None and not geom.is_empty else None,
                        crs=layer_crs,
                        properties={str(k): _json_safe(v) for k, v in props.items()},
                        measurement_status=result.status,
                        measurements=result.values,
                        measurement_crs=result.crs,
                        measurement_note=result.note,
                    )
                )
                index += 1

        if not features:
            raise ingest.IngestError("No features were found in the file.")

        with transaction.atomic():
            Feature.objects.bulk_create(features, batch_size=BATCH_SIZE)
            upload.feature_count = len(features)
            upload.crs = crs_labels.pop() if len(crs_labels) == 1 else "MIXED"
            upload.status = UploadedFile.Status.COMPLETED
            upload.error_message = ""
            upload.save()
    except ingest.IngestError as exc:
        _fail(upload, str(exc))
    except Exception:
        logger.exception("Unexpected error processing file %s", upload.id)
        _fail(upload, "Unexpected error while processing the file.")
    return upload


def _path(upload: UploadedFile) -> Path:
    return Path(upload.file.path)


def _fail(upload: UploadedFile, message: str) -> None:
    upload.status = UploadedFile.Status.FAILED
    upload.error_message = message
    upload.save(update_fields=["status", "error_message"])


def _json_safe(value):
    """Make attribute values JSON-serialisable (NaN/NaT/numpy/Timestamp -> plain Python)."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Timestamp,)) or hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
