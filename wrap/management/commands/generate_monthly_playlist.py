"""
Management command: generate_monthly_playlist

Orquesta el "wrap" mensual:
  1. Calcula el mes anterior a la fecha de ejecución (o el forzado por flags).
  2. Pide a Spotify las top tracks (short_term, 30 por defecto).
  3. Crea la playlist "<mes> <año>" (pública por defecto, visible en tu perfil) y añade las canciones.
  4. Genera la portada del mes y la sube.
  5. Registra el resultado en ExecutionLog.

Pensado para lanzarse el día 1 de cada mes vía cron (Linux) o Task Scheduler.

Ejemplos:
    python manage.py generate_monthly_playlist
    python manage.py generate_monthly_playlist --dry-run          # solo preview.jpg
    python manage.py generate_monthly_playlist --private
    python manage.py generate_monthly_playlist --month 1 --year 2026
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from wrap.models import ExecutionLog
from wrap.services import cover_generator
from wrap.services.spotify_client import SpotifyClient, SpotifyConfigError

logger = logging.getLogger("wrap.command")


class Command(BaseCommand):
    help = "Crea la playlist del mes anterior con tus top tracks y una portada generada."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Solo genera preview.jpg de la portada; no toca Spotify ni la BBDD.",
        )
        parser.add_argument(
            "--private", action="store_true",
            help="Crea la playlist privada (por defecto es pública y visible en tu perfil).",
        )
        parser.add_argument(
            "--month", type=int, default=None,
            help="Forzar el mes objetivo (1-12) en lugar del mes anterior.",
        )
        parser.add_argument(
            "--year", type=int, default=None,
            help="Forzar el año objetivo (se usa junto con --month).",
        )
        parser.add_argument(
            "--limit", type=int, default=30,
            help="Número de canciones a incluir (1-50, por defecto 30).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Crear la playlist aunque ya exista otra con el mismo nombre.",
        )

    def handle(self, *args, **opts):
        mes, anio = self._resolver_mes(opts["month"], opts["year"])
        limite = self._validar_limite(opts["limit"])
        publica = not opts["private"]
        mes_nombre = cover_generator.MESES[mes - 1].capitalize()  # "April"
        periodo = f"{mes_nombre} {anio}"                           # "April 2026" — para logs y descripción
        # Nombre real de la playlist en Spotify: "April '26 was..." (estilo abierto).
        etiqueta = f"{mes_nombre} '{anio % 100:02d} was..."

        logger.info("=== Wrap mensual: %s (dry_run=%s) ===", periodo, opts["dry_run"])

        # --dry-run: solo generamos el preview local y salimos (no toca nada externo).
        if opts["dry_run"]:
            ruta = cover_generator.guardar_preview(mes, anio)
            self.stdout.write(self.style.SUCCESS(
                f"[dry-run] Portada de '{periodo}' en {ruta}. No se ha tocado Spotify ni la BBDD."
            ))
            return

        # La BBDD debe estar migrada; si no, el ExecutionLog final fallaría.
        self._verificar_migraciones()

        playlist_id, playlist_url, track_count = "", "", 0
        try:
            cliente = SpotifyClient()

            # Evitar duplicados: si ya existe una playlist con ese nombre, no creamos
            # otra (salvo --force). Salida limpia, sin registrar fallo.
            if not opts["force"]:
                existente = cliente.find_playlist_by_name(etiqueta)
                if existente:
                    url = existente.get("external_urls", {}).get("spotify", "") or existente["id"]
                    msg = (f"Ya existe una playlist '{etiqueta}' ({url}). "
                           f"No se crea otra; usa --force para duplicarla.")
                    logger.info(msg)
                    self.stdout.write(self.style.WARNING(msg))
                    return

            tracks = cliente.get_top_tracks(time_range="short_term", limit=limite)
            if not tracks:
                raise CommandError(
                    "Spotify no devolvió canciones (¿cuenta sin escuchas en las últimas semanas?)."
                )
            uris = [t["uri"] for t in tracks]
            track_count = len(uris)

            descripcion = (
                f"Tus {track_count} canciones más escuchadas de {periodo}. "
                f"Generada automáticamente."
            )
            playlist = cliente.create_playlist(
                name=etiqueta, public=publica, description=descripcion
            )
            playlist_id = playlist["id"]
            playlist_url = playlist.get("external_urls", {}).get("spotify", "")

            cliente.add_tracks(playlist_id, uris)

            portada_b64, _ = cover_generator.generar_base64(mes, anio)
            cliente.upload_cover(playlist_id, portada_b64)

            ExecutionLog.objects.create(
                target_label=etiqueta, target_month=mes, target_year=anio,
                success=True, playlist_id=playlist_id, playlist_url=playlist_url,
                track_count=track_count,
            )
            logger.info("Wrap completado: %s -> %s", etiqueta, playlist_url or playlist_id)
            self.stdout.write(self.style.SUCCESS(
                f"✓ Playlist '{etiqueta}' creada con {track_count} canciones: "
                f"{playlist_url or playlist_id}"
            ))

        except SpotifyConfigError as exc:
            self._registrar_fallo(mes, anio, etiqueta, playlist_id, playlist_url, track_count, exc)
            raise CommandError(f"Configuración incompleta: {exc}")
        except CommandError as exc:
            # Errores de negocio ya legibles (p. ej. sin canciones): los registramos y relanzamos.
            self._registrar_fallo(mes, anio, etiqueta, playlist_id, playlist_url, track_count, exc)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fallo generando el wrap de %s", etiqueta)
            self._registrar_fallo(mes, anio, etiqueta, playlist_id, playlist_url, track_count, exc)
            raise CommandError(f"Error generando el wrap: {exc}")

    # ------------------------------------------------------------------ helpers

    def _resolver_mes(self, mes_opt, anio_opt):
        """Devuelve (mes, año) objetivo: el forzado por flags o el mes anterior a hoy."""
        if (mes_opt is None) ^ (anio_opt is None):
            raise CommandError("Usa --month y --year juntos, o ninguno de los dos.")
        if mes_opt is not None:
            if not 1 <= mes_opt <= 12:
                raise CommandError("--month debe estar entre 1 y 12.")
            return mes_opt, anio_opt
        # Mes anterior: retrocedemos al día 1 de hoy y restamos un día.
        hoy = timezone.localdate()
        ultimo_del_mes_anterior = hoy.replace(day=1) - timedelta(days=1)
        return ultimo_del_mes_anterior.month, ultimo_del_mes_anterior.year

    def _validar_limite(self, limite):
        if not 1 <= limite <= 50:
            raise CommandError("--limit debe estar entre 1 y 50 (límite de la API de Spotify).")
        return limite

    def _verificar_migraciones(self):
        """Aborta con un mensaje claro si hay migraciones sin aplicar."""
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        pendientes = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pendientes:
            raise CommandError(
                "Hay migraciones sin aplicar (la base de datos no está lista). "
                "Ejecuta primero:  python manage.py migrate"
            )

    def _registrar_fallo(self, mes, anio, etiqueta, playlist_id, playlist_url, track_count, exc):
        """Guarda un ExecutionLog de fallo para tener traza en BBDD."""
        ExecutionLog.objects.create(
            target_label=etiqueta, target_month=mes, target_year=anio,
            success=False, playlist_id=playlist_id, playlist_url=playlist_url,
            track_count=track_count, error_message=str(exc),
        )
