import uuid

from django.db import models


class UploadedFile(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        PROCESSING = "PROCESSING"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"

    class FileType(models.TextChoices):
        SHAPEFILE = "SHAPEFILE"
        KML = "KML"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=16, choices=FileType.choices)
    file = models.FileField(upload_to="uploads/%Y/%m/")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True, default="")
    # Source CRS of the file, e.g. "EPSG:4326". "MIXED" if layers disagree.
    crs = models.CharField(max_length=128, blank=True, default="")
    feature_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.status})"


class Feature(models.Model):
    class MeasurementStatus(models.TextChoices):
        MEASURED = "MEASURED"
        NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. points: nothing to measure
        UNSUPPORTED = "UNSUPPORTED"  # e.g. GeometryCollection, empty/null geometry
        ERROR = "ERROR"  # unexpected failure while measuring this feature

    file = models.ForeignKey(UploadedFile, on_delete=models.CASCADE, related_name="features")
    index = models.PositiveIntegerField()  # 0-based, stable across layers
    layer = models.CharField(max_length=255, blank=True, default="")
    geometry_type = models.CharField(max_length=32)
    geometry = models.JSONField(null=True)  # GeoJSON geometry, in the file's source CRS
    crs = models.CharField(max_length=128)
    properties = models.JSONField(default=dict)

    measurement_status = models.CharField(max_length=16, choices=MeasurementStatus.choices)
    measurements = models.JSONField(default=dict)  # e.g. {"area_sq_m": ...}
    measurement_crs = models.CharField(max_length=255, blank=True, default="")
    measurement_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["index"]
        constraints = [models.UniqueConstraint(fields=["file", "index"], name="uniq_feature_index_per_file")]
