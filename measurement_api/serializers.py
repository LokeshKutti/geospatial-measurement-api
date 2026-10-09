import os

from django.conf import settings
from rest_framework import serializers

from .models import Feature, UploadedFile

EXTENSIONS = {".zip": UploadedFile.FileType.SHAPEFILE, ".kml": UploadedFile.FileType.KML}


class FileUploadSerializer(serializers.Serializer):
    """Validates the multipart upload. Persistence/processing happens in the view."""

    file = serializers.FileField()

    def validate_file(self, f):
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in EXTENSIONS:
            raise serializers.ValidationError("Unsupported file type. Upload a .zip (Shapefile) or a .kml file.")
        if f.size == 0:
            raise serializers.ValidationError("The uploaded file is empty.")
        if f.size > settings.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError(
                f"File too large. Maximum size is {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            )
        return f

    def create(self, validated_data):
        f = validated_data["file"]
        ext = os.path.splitext(f.name)[1].lower()
        return UploadedFile.objects.create(
            filename=os.path.basename(f.name)[:255], file_type=EXTENSIONS[ext], file=f
        )


class UploadedFileSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    error = serializers.CharField(source="error_message", read_only=True)

    class Meta:
        model = UploadedFile
        fields = ["id", "filename", "file_type", "feature_count", "crs", "status", "error", "created_at"]


class FeatureSerializer(serializers.ModelSerializer):
    feature_id = serializers.IntegerField(source="index", read_only=True)

    class Meta:
        model = Feature
        fields = ["feature_id", "layer", "geometry_type", "geometry", "crs", "properties"]


class MeasurementSerializer(serializers.ModelSerializer):
    feature_id = serializers.IntegerField(source="index", read_only=True)
    status = serializers.CharField(source="measurement_status", read_only=True)
    projected_crs = serializers.CharField(source="measurement_crs", read_only=True)
    note = serializers.CharField(source="measurement_note", read_only=True)

    class Meta:
        model = Feature
        fields = ["feature_id", "layer", "geometry_type", "crs", "status", "measurements", "projected_crs", "note"]
