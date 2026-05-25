from django.db import models


class ExecutionLog(models.Model):
    """Registro de cada ejecución del comando generate_monthly_playlist."""

    executed_at = models.DateTimeField(auto_now_add=True, verbose_name="ejecutado el")
    target_label = models.CharField(
        max_length=64, verbose_name="etiqueta del mes"
    )  # p. ej. "january 2026"
    target_month = models.PositiveSmallIntegerField(verbose_name="mes objetivo")
    target_year = models.PositiveIntegerField(verbose_name="año objetivo")

    success = models.BooleanField(default=False, verbose_name="éxito")
    playlist_id = models.CharField(max_length=64, blank=True, verbose_name="ID de playlist")
    playlist_url = models.URLField(blank=True, verbose_name="URL de playlist")
    track_count = models.PositiveSmallIntegerField(default=0, verbose_name="nº de canciones")
    error_message = models.TextField(blank=True, verbose_name="mensaje de error")

    class Meta:
        verbose_name = "ejecución"
        verbose_name_plural = "ejecuciones"
        ordering = ["-executed_at"]

    def __str__(self):
        estado = "OK" if self.success else "FALLO"
        return f"[{estado}] {self.target_label} ({self.executed_at:%Y-%m-%d %H:%M})"
