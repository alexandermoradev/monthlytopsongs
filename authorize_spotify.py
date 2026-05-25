#!/usr/bin/env python
"""
authorize_spotify.py — Autorización OAuth2 de Spotify (SE EJECUTA UNA SOLA VEZ).

Lanza el flujo "Authorization Code" para obtener un refresh_token persistente que
luego usará la app en cada ejecución mensual sin volver a pedir login.

REQUISITOS PREVIOS:
  1. Crear una app en https://developer.spotify.com/dashboard
  2. Copiar su Client ID y Client Secret a .env (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET)
  3. Registrar en el dashboard el MISMO Redirect URI que tienes en .env
     (por defecto: http://127.0.0.1:8888/callback)

USO:
    .venv/bin/python authorize_spotify.py

Al terminar, te mostrará el refresh_token y te ofrecerá guardarlo en .env.
"""

import sys
from pathlib import Path

import environ
import spotipy
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError

# Raíz del proyecto (este script vive en la raíz).
BASE_DIR = Path(__file__).resolve().parent
RUTA_ENV = BASE_DIR / ".env"

# Scopes necesarios:
#   user-top-read           -> leer /me/top/tracks
#   playlist-modify-public  -> crear/editar playlists públicas
#   playlist-modify-private -> crear/editar playlists privadas
#   ugc-image-upload        -> subir la portada personalizada
SCOPES = (
    "user-top-read "
    "playlist-modify-public "
    "playlist-modify-private "
    "ugc-image-upload"
)


def _leer_credenciales():
    """Lee client_id, client_secret y redirect_uri del .env."""
    env = environ.Env()
    if RUTA_ENV.exists():
        environ.Env.read_env(RUTA_ENV)

    client_id = env("SPOTIFY_CLIENT_ID", default="")
    client_secret = env("SPOTIFY_CLIENT_SECRET", default="")
    redirect_uri = env(
        "SPOTIFY_REDIRECT_URI", default="http://127.0.0.1:8888/callback"
    )

    # Validación temprana: sin credenciales no tiene sentido continuar.
    if not client_id or not client_secret:
        print("✗ Faltan credenciales en .env.")
        print("  Rellena SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET y vuelve a ejecutar.")
        print("  (Las obtienes en https://developer.spotify.com/dashboard)")
        sys.exit(1)

    return client_id, client_secret, redirect_uri


def _guardar_en_env(refresh_token):
    """Reemplaza (o añade) la línea SPOTIFY_REFRESH_TOKEN en .env de forma segura."""
    lineas = RUTA_ENV.read_text(encoding="utf-8").splitlines()
    salida = []
    encontrada = False
    for linea in lineas:
        if linea.startswith("SPOTIFY_REFRESH_TOKEN="):
            salida.append(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
            encontrada = True
        else:
            salida.append(linea)
    if not encontrada:
        salida.append(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
    RUTA_ENV.write_text("\n".join(salida) + "\n", encoding="utf-8")


def main():
    client_id, client_secret, redirect_uri = _leer_credenciales()

    print("→ Iniciando flujo OAuth de Spotify…")
    print(f"  Redirect URI usado: {redirect_uri}")
    print("  (Debe coincidir EXACTAMENTE con el registrado en el dashboard.)\n")

    # MemoryCacheHandler: no deja fichero .cache en disco; el refresh_token
    # lo gestionamos nosotros guardándolo en .env.
    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=MemoryCacheHandler(),
        open_browser=True,
    )

    try:
        # Abre el navegador y levanta un mini servidor local para capturar
        # el código de autorización del redirect. Si el navegador no se abre,
        # spotipy te pedirá que pegues la URL de redirección a mano.
        code = auth_manager.get_auth_response()
        # Intercambia el código por los tokens (se guardan en el cache_handler).
        auth_manager.get_access_token(code, as_dict=False, check_cache=False)
    except SpotifyOauthError as exc:
        print(f"\n✗ Error en la autorización: {exc}")
        print("  Revisa client id/secret y que el Redirect URI coincida.")
        sys.exit(1)

    token_info = auth_manager.cache_handler.get_cached_token()
    refresh_token = token_info["refresh_token"]

    # Verificación: usamos el access token para identificar la cuenta.
    sp = spotipy.Spotify(auth=token_info["access_token"])
    usuario = sp.current_user()
    nombre = usuario.get("display_name") or usuario.get("id")

    print(f"\n✓ Autorización correcta. Cuenta: {nombre}")
    print("\n  Tu refresh_token es:\n")
    print(f"    {refresh_token}\n")

    # Ofrecemos guardarlo automáticamente en .env.
    respuesta = input("¿Guardarlo automáticamente en .env? [S/n]: ").strip().lower()
    if respuesta in ("", "s", "si", "sí", "y", "yes"):
        _guardar_en_env(refresh_token)
        print("✓ Guardado en .env (SPOTIFY_REFRESH_TOKEN). ¡Listo!")
    else:
        print("→ No se ha tocado .env. Pega el valor a mano en SPOTIFY_REFRESH_TOKEN.")


if __name__ == "__main__":
    main()
