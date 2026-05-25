"""
spotify_client.py — Wrapper sobre spotipy para el wrap mensual.

Encapsula toda la interacción con la API de Spotify:
  - Refresco automático del access token a partir del refresh_token persistido.
  - Lectura de las top tracks.
  - Creación de playlist y adición de canciones.
  - Subida de la portada personalizada.

Todas las llamadas de red pasan por un mecanismo de REINTENTOS con BACKOFF
EXPONENCIAL (3 intentos) implementado a mano con la librería estándar.
"""

import logging
import time

import requests
import spotipy
from django.conf import settings
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

logger = logging.getLogger("wrap.spotify")

# Mismos scopes que en la autorización inicial.
SCOPES = (
    "user-top-read "
    "playlist-modify-public "
    "playlist-modify-private "
    "ugc-image-upload"
)

# Parámetros del backoff exponencial.
MAX_INTENTOS = 3
ESPERA_BASE = 1.0  # segundos; se duplica en cada reintento (1s, 2s, 4s…)


class SpotifyConfigError(RuntimeError):
    """Falta configuración necesaria (credenciales o refresh_token)."""


def _es_reintentable(exc):
    """Decide si un error merece reintento (transitorio) o no.

    Reintentables: errores de red y respuestas 429 (rate limit) o 5xx.
    NO reintentables: 401/403/404… (fallos de credenciales/permisos/recurso).
    """
    if isinstance(exc, requests.RequestException):
        return True
    if isinstance(exc, SpotifyException):
        estado = exc.http_status
        return estado == 429 or (estado is not None and estado >= 500)
    return False


def _con_reintentos(descripcion, funcion, *args, **kwargs):
    """Ejecuta `funcion` con reintentos y backoff exponencial.

    `descripcion` es texto legible para los logs (p. ej. "crear playlist").
    Si tras MAX_INTENTOS sigue fallando, relanza la última excepción.
    """
    espera = ESPERA_BASE
    for intento in range(1, MAX_INTENTOS + 1):
        try:
            return funcion(*args, **kwargs)
        except (SpotifyException, requests.RequestException) as exc:
            # Errores no transitorios: no tiene sentido reintentar.
            if not _es_reintentable(exc):
                logger.error("Error no recuperable al %s: %s", descripcion, exc)
                raise

            # Último intento agotado: relanzamos.
            if intento == MAX_INTENTOS:
                logger.error(
                    "Fallo definitivo al %s tras %d intentos: %s",
                    descripcion, MAX_INTENTOS, exc,
                )
                raise

            # Si es rate limit (429) y Spotify indica Retry-After, lo respetamos.
            espera_actual = espera
            if isinstance(exc, SpotifyException) and exc.http_status == 429:
                retry_after = (exc.headers or {}).get("Retry-After")
                if retry_after:
                    espera_actual = max(espera, float(retry_after))

            logger.warning(
                "Fallo al %s (intento %d/%d): %s. Reintentando en %.1fs…",
                descripcion, intento, MAX_INTENTOS, exc, espera_actual,
            )
            time.sleep(espera_actual)
            espera *= 2  # backoff exponencial


class SpotifyClient:
    """Cliente de alto nivel para el wrap mensual."""

    def __init__(self):
        # Validación de configuración antes de tocar la red.
        if not settings.SPOTIFY_CLIENT_ID or not settings.SPOTIFY_CLIENT_SECRET:
            raise SpotifyConfigError(
                "Faltan SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET en .env."
            )
        if not settings.SPOTIFY_REFRESH_TOKEN:
            raise SpotifyConfigError(
                "Falta SPOTIFY_REFRESH_TOKEN en .env. "
                "Ejecuta primero authorize_spotify.py."
            )

        # Auth manager solo para refrescar el token (sin caché en disco).
        self._auth_manager = SpotifyOAuth(
            client_id=settings.SPOTIFY_CLIENT_ID,
            client_secret=settings.SPOTIFY_CLIENT_SECRET,
            redirect_uri=settings.SPOTIFY_REDIRECT_URI,
            scope=SCOPES,
            cache_handler=MemoryCacheHandler(),
        )

        # Refrescamos el access token al arrancar (válido ~1h, suficiente para el run).
        logger.info("Refrescando access token de Spotify…")
        token_info = _con_reintentos(
            "refrescar el access token",
            self._auth_manager.refresh_access_token,
            settings.SPOTIFY_REFRESH_TOKEN,
        )
        self._sp = spotipy.Spotify(auth=token_info["access_token"])

        # Identificamos al usuario (su id se necesita para crear la playlist).
        usuario = _con_reintentos("identificar al usuario", self._sp.current_user)
        self.user_id = usuario["id"]
        self.display_name = usuario.get("display_name") or usuario["id"]
        logger.info("Autenticado como '%s' (id=%s).", self.display_name, self.user_id)

    def get_top_tracks(self, time_range="short_term", limit=30):
        """Devuelve la lista de top tracks (dicts de la API) del rango indicado."""
        logger.info("Obteniendo top %d tracks (time_range=%s)…", limit, time_range)
        resultado = _con_reintentos(
            "obtener las top tracks",
            self._sp.current_user_top_tracks,
            limit=limit,
            time_range=time_range,
        )
        items = resultado.get("items", [])
        logger.info("Recibidas %d canciones.", len(items))
        return items

    def create_playlist(self, name, public=False, description=""):
        """Crea una playlist para el usuario y devuelve el dict de la API."""
        logger.info("Creando playlist '%s' (public=%s)…", name, public)
        playlist = _con_reintentos(
            "crear la playlist",
            self._sp.user_playlist_create,
            user=self.user_id,
            name=name,
            public=public,
            description=description,
        )
        logger.info("Playlist creada: id=%s", playlist["id"])
        return playlist

    def find_playlist_by_name(self, name):
        """Busca una playlist PROPIA con ese nombre exacto. Devuelve el dict o None.

        Pagina por todas las playlists del usuario y filtra por propietario para
        no confundir con playlists que solo sigue.
        """
        offset = 0
        while True:
            pagina = _con_reintentos(
                "listar tus playlists",
                self._sp.current_user_playlists,
                limit=50, offset=offset,
            )
            for pl in pagina.get("items", []):
                if pl and pl.get("name") == name and pl["owner"]["id"] == self.user_id:
                    return pl
            if pagina.get("next"):
                offset += 50
            else:
                return None

    def add_tracks(self, playlist_id, track_uris):
        """Añade canciones a la playlist (máx. 100 por llamada; aquí son 30)."""
        logger.info("Añadiendo %d canciones a la playlist…", len(track_uris))
        _con_reintentos(
            "añadir las canciones",
            self._sp.playlist_add_items,
            playlist_id,
            track_uris,
        )

    def upload_cover(self, playlist_id, jpeg_base64):
        """Sube la portada (JPEG en base64, sin prefijo data:) a la playlist."""
        logger.info("Subiendo portada personalizada…")
        _con_reintentos(
            "subir la portada",
            self._sp.playlist_upload_cover_image,
            playlist_id,
            jpeg_base64,
        )
        logger.info("Portada subida correctamente.")
