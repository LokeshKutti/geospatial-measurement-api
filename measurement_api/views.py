from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import UploadedFile
from .serializers import (
    FeatureSerializer,
    FileUploadSerializer,
    MeasurementSerializer,
    UploadedFileSerializer,
)
from .services.processing import process_uploaded_file


class FileViewSet(
    mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet
):
    queryset = UploadedFile.objects.all()
    serializer_class = UploadedFileSerializer

    @extend_schema(request={"multipart/form-data": FileUploadSerializer}, responses={201: UploadedFileSerializer})
    def create(self, request, *args, **kwargs):
        """Upload a `.zip` (Shapefile) or `.kml`. The file is processed synchronously.

        201 when processed, 422 (with the stored record) when the file is readable
        as an upload but not as geospatial data, 400 for invalid uploads.
        """
        upload_serializer = FileUploadSerializer(data=request.data)
        upload_serializer.is_valid(raise_exception=True)
        upload = process_uploaded_file(upload_serializer.save())
        code = (
            status.HTTP_201_CREATED
            if upload.status == UploadedFile.Status.COMPLETED
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return Response(UploadedFileSerializer(upload).data, status=code)

    @extend_schema(responses=MeasurementSerializer(many=True))
    @action(detail=True, methods=["get"])
    def measurements(self, request, pk=None):
        """Per-feature measurements (area for polygons, length for lines)."""
        return self._features_response(request, MeasurementSerializer)

    @extend_schema(responses=FeatureSerializer(many=True))
    @action(detail=True, methods=["get"])
    def features(self, request, pk=None):
        """Per-feature geometry (GeoJSON, source CRS) and attributes."""
        return self._features_response(request, FeatureSerializer)

    def _features_response(self, request, serializer_class):
        upload = self.get_object()
        if upload.status != UploadedFile.Status.COMPLETED:
            return Response(
                {"detail": f"File is not ready (status: {upload.status}).", "error": upload.error_message},
                status=status.HTTP_409_CONFLICT,
            )
        qs = upload.features.all()
        if (layer := request.query_params.get("layer")):
            qs = qs.filter(layer=layer)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(serializer_class(page, many=True).data)
