"""
snapadmin/api/exports.py

Asynchronous background export API (issue #6).

    POST   /api/exports/                 start an export → returns a job (id + status)
    GET    /api/exports/                 list your export jobs
    GET    /api/exports/<id>/            poll status + progress (rows, %, ETA)
    POST   /api/exports/<id>/cancel/     request cancellation
    GET    /api/exports/<id>/download/   download the finished file

Only ``SnapModel`` targets are exportable, and the caller must hold the model's
``view`` permission (token ``allowed_models`` scope applies). Jobs are private to
their requester; superusers see all.
"""

import os

from django.apps import apps
from django.http import FileResponse
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from snapadmin.api.authentication import SnapAPIAuthMixin, token_has_permission
from snapadmin.exporting import export_enabled, output_path
from snapadmin.models import APIToken, SnapExportJob, SnapModel


class ExportJobSerializer(serializers.ModelSerializer):
    progress_percent = serializers.IntegerField(read_only=True)
    eta_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = SnapExportJob
        fields = [
            "id", "app_label", "model", "export_format", "filters", "status",
            "total_rows", "processed_rows", "progress_percent", "eta_seconds",
            "error", "created_at", "started_at", "finished_at", "download_url",
        ]
        read_only_fields = [
            "id", "status", "total_rows", "processed_rows", "error",
            "created_at", "started_at", "finished_at",
        ]

    def get_download_url(self, obj) -> str | None:
        if obj.status != SnapExportJob.Status.COMPLETED:
            return None
        request = self.context.get("request")
        url = f"/api/exports/{obj.pk}/download/"
        return request.build_absolute_uri(url) if request else url


class ExportJobCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SnapExportJob
        fields = ["app_label", "model", "export_format", "filters"]

    def validate(self, attrs):
        app_label, model_name = attrs["app_label"], attrs["model"]
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            raise serializers.ValidationError(f"Unknown model '{app_label}.{model_name}'.")
        if not (isinstance(model, type) and issubclass(model, SnapModel) and model is not SnapModel):
            raise serializers.ValidationError("Only SnapModel-backed models can be exported.")

        request = self.context["request"]
        if not _can_view(request, app_label, model_name):
            raise serializers.ValidationError("You do not have permission to export this model.")
        return attrs


def _can_view(request, app_label: str, model_name: str) -> bool:
    """Mirror the model-view permission check used by the CRUD API."""
    token = getattr(request, "auth", None)
    if isinstance(token, APIToken):
        return token_has_permission(token, request.user, app_label, model_name, "view")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    return user.has_perm(f"{app_label}.view_{model_name.lower()}")


class ExportJobViewSet(
    SnapAPIAuthMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = SnapExportJob.objects.all()
        if not self.request.user.is_superuser:
            qs = qs.filter(requested_by=self.request.user)
        return qs

    def get_serializer_class(self):
        return ExportJobCreateSerializer if self.action == "create" else ExportJobSerializer

    def create(self, request, *args, **kwargs):
        if not export_enabled():
            return Response({"detail": "Background export is disabled."},
                            status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Enqueue the worker (runs synchronously under CELERY_TASK_ALWAYS_EAGER).
        # Celery is an optional dependency — surface a clear 503 instead of a raw
        # ModuleNotFoundError (500) when it is not installed/configured.
        try:
            from snapadmin.tasks import run_export
        except ImportError:
            return Response(
                {"detail": "Background export requires Celery. Install it with "
                           "`pip install django-snapadmin[celery]` and configure a broker "
                           "(CELERY_BROKER_URL)."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        job = SnapExportJob.objects.create(requested_by=request.user, **serializer.validated_data)
        run_export.delay(str(job.pk))

        job.refresh_from_db()
        output = ExportJobSerializer(job, context={"request": request})
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self.get_object()
        if job.is_finished:
            return Response({"detail": f"Job already {job.status}."},
                            status=status.HTTP_409_CONFLICT)
        job.status = SnapExportJob.Status.CANCELLED
        job.save(update_fields=["status"])
        return Response(ExportJobSerializer(job, context={"request": request}).data)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        if job.status != SnapExportJob.Status.COMPLETED:
            return Response({"detail": f"Job is '{job.status}', not ready for download."},
                            status=status.HTTP_409_CONFLICT)
        path = output_path(job)
        if not os.path.exists(path):
            return Response({"detail": "Export file is no longer available."},
                            status=status.HTTP_410_GONE)
        content_type = "text/csv" if job.export_format == SnapExportJob.Format.CSV else "application/x-ndjson"
        return FileResponse(open(path, "rb"), as_attachment=True,
                            filename=job.file_name, content_type=content_type)
