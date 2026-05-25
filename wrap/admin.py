from django.contrib import admin

from .models import ExecutionLog


@admin.register(ExecutionLog)
class ExecutionLogAdmin(admin.ModelAdmin):
    """Vista de solo lectura de las ejecuciones (son registros históricos)."""

    list_display = ("executed_at", "target_label", "success", "track_count", "playlist_id")
    list_filter = ("success", "target_year")
    search_fields = ("target_label", "playlist_id")
    date_hierarchy = "executed_at"
    # Los logs son inmutables desde el admin: todos los campos en solo lectura.
    readonly_fields = (
        "executed_at", "target_label", "target_month", "target_year",
        "success", "playlist_id", "playlist_url", "track_count", "error_message",
    )

    def has_add_permission(self, request):
        return False
