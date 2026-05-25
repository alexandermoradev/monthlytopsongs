# Monthly Top Songs — Spotify Wrap mensual automático

App Django que, el día 1 de cada mes, crea en tu cuenta de Spotify una playlist
con tus **30 canciones más escuchadas del mes anterior** y le pone una **portada
generada dinámicamente** (degradado tipo la referencia, un color por mes).

- Título de la playlist: `"<mes en inglés> <año>"` → `january 2026`.
- Portada: JPEG 640×640 con gradiente luminoso (12 paletas, una por mes) y los
  textos `alex` / mes / año en blanco (Inter Black).
- Registro de cada ejecución en SQLite (`ExecutionLog`, visible en el admin).
- Pensado para lanzarse con **cron** (Linux) o **Task Scheduler** (Windows).

---

## 1. Requisitos

- Python 3.11+ (probado con 3.12).
- Una cuenta de Spotify y una app en el dashboard de desarrolladores (gratis).

## 2. Instalación

```bash
git clone <tu-repo> MonthlyTopSongs   # o descomprime el proyecto
cd MonthlyTopSongs

python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Crear la app de Spotify

1. Entra en <https://developer.spotify.com/dashboard> → **Create app**.
2. En **Redirect URIs** añade EXACTAMENTE: `http://127.0.0.1:8888/callback`
3. Anota el **Client ID** y el **Client Secret**.

## 4. Configurar el `.env`

Copia la plantilla y rellena los valores:

```bash
cp .env.example .env
```

```ini
SECRET_KEY='...'                 # ya generada; o crea otra (ver .env.example)
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

SPOTIFY_CLIENT_ID=...            # del dashboard
SPOTIFY_CLIENT_SECRET=...        # del dashboard
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_REFRESH_TOKEN=           # lo rellena el paso 6
```

## 5. Migraciones

```bash
python manage.py migrate
```

## 6. Autorización inicial de Spotify (UNA SOLA VEZ)

```bash
python authorize_spotify.py
```

Se abre el navegador, autorizas los permisos y el script captura el
`refresh_token`, **verifica la cuenta** y te ofrece guardarlo automáticamente en
`.env`. A partir de aquí, las ejecuciones mensuales refrescan el token solas.

Scopes usados: `user-top-read`, `playlist-modify-public`,
`playlist-modify-private`, `ugc-image-upload`.

## 7. Uso manual

```bash
# Crea la playlist del MES ANTERIOR (privada):
python manage.py generate_monthly_playlist

# Solo generar la portada para validarla (no toca Spotify ni la BBDD):
python manage.py generate_monthly_playlist --dry-run   # -> preview.jpg

# Pública:
python manage.py generate_monthly_playlist --public

# Forzar un mes concreto (para pruebas):
python manage.py generate_monthly_playlist --month 1 --year 2026

# Cambiar el nº de canciones (1-50):
python manage.py generate_monthly_playlist --limit 50
```

> **Sobre el "mes anterior":** se calcula con la hora local (`TIME_ZONE` en
> `core/settings.py`, ahora `Europe/Madrid`). Si vives en otra zona, cámbialo.

---

## 8. Programación automática

### 8a. cron (Arch Linux) — recomendado

Edita tu crontab:

```bash
crontab -e
```

Añade esta línea para ejecutar **el día 1 de cada mes a las 09:00**:

```cron
0 9 1 * * /home/alex/PycharmProjects/MonthlyTopSongs/run_monthly.sh >> /home/alex/PycharmProjects/MonthlyTopSongs/logs/cron.log 2>&1
```

Campos de cron: `min hora día-del-mes mes día-de-semana`. cron usa la **hora
local del sistema** (coincide con `TIME_ZONE`). El script `run_monthly.sh` ya
hace `cd` al proyecto y usa el Python del `.venv`, así que no necesitas activar
el entorno.

### 8b. systemd timer (alternativa más robusta para portátiles)

Como `bspwm` suele ir en un equipo que **no está encendido 24/7**, cron se
*saltaría* la ejecución si el PC está apagado el día 1. Un timer de systemd con
`Persistent=true` la ejecuta en el siguiente arranque. Crea estos dos ficheros
de usuario:

`~/.config/systemd/user/monthly-wrap.service`
```ini
[Unit]
Description=Spotify Monthly Wrap

[Service]
Type=oneshot
ExecStart=/home/alex/PycharmProjects/MonthlyTopSongs/run_monthly.sh
```

`~/.config/systemd/user/monthly-wrap.timer`
```ini
[Unit]
Description=Lanza el wrap el día 1 de cada mes

[Timer]
OnCalendar=*-*-01 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Actívalo:
```bash
systemctl --user daemon-reload
systemctl --user enable --now monthly-wrap.timer
systemctl --user list-timers monthly-wrap.timer   # comprobar próxima ejecución
```

(Para que corra aunque no haya sesión iniciada: `loginctl enable-linger alex`.)

### 8c. Task Scheduler (Windows) — equivalente documentado

Con `schtasks` (PowerShell/CMD como administrador), día 1 de cada mes a las 9:00:

```bat
schtasks /Create /SC MONTHLY /D 1 /TN "SpotifyMonthlyWrap" /ST 09:00 ^
  /TR "C:\ruta\a\MonthlyTopSongs\run_monthly.bat"
```

O por la GUI: *Programador de tareas* → *Crear tarea básica* → desencadenador
**Mensual**, día **1** → acción **Iniciar un programa** → `run_monthly.bat`.

---

## 9. Logs y registro de ejecuciones

- Log rotativo de la app: `logs/monthly_wrap.log` (1 MB × 5 ficheros).
- Salida de cron: `logs/cron.log`.
- Historial en BBDD: modelo `ExecutionLog` (fecha, éxito/fallo, ID/URL de
  playlist, nº de canciones, error). Para verlo en el admin:

```bash
python manage.py createsuperuser
python manage.py runserver
# -> http://127.0.0.1:8000/admin/  (sección "ejecuciones")
```

---

## 10. Personalizar la portada

Todo en `wrap/services/cover_generator.py` (constantes arriba del fichero):

- `HUE_TOP_ENERO`, `PASO_HUE`, `SPAN_HUE` → tonos y rotación por mes.
- `SAT_PICO`, `SAT_BORDE`, `L_OBJETIVO`, `VALOR` → saturación, contraste y brillo.
- `TOP_TEXT_*`, `BIG_TEXT_*`, `*_Y_FRAC` → tamaños y posición de los textos.

Previsualizar:
```bash
python wrap/services/cover_generator.py            # preview.jpg (enero)
python wrap/services/cover_generator.py montaje    # previews_12_meses.jpg (los 12)
```

La fuente Inter (variable) está en `assets/fonts/Inter-Variable.ttf`.

---

## 11. Limitaciones conocidas

- Spotify `/me/top/tracks` con `time_range=short_term` representa las
  **~4 semanas** previas (según su documentación oficial), pero es una ventana
  **móvil**, no el mes natural exacto. Por tanto el título `january 2026` es una
  etiqueta amigable, no un cálculo calendárico estricto. La API no ofrece el top
  de un mes natural concreto.

## 12. Estructura del proyecto

```
MonthlyTopSongs/
├── manage.py
├── authorize_spotify.py          # OAuth inicial (one-shot)
├── run_monthly.sh / .bat         # lanzadores para cron / Task Scheduler
├── requirements.txt
├── .env / .env.example
├── assets/fonts/Inter-Variable.ttf
├── logs/                         # monthly_wrap.log, cron.log
├── core/                         # settings, urls, wsgi/asgi
└── wrap/
    ├── models.py                 # ExecutionLog
    ├── admin.py
    ├── management/commands/generate_monthly_playlist.py
    └── services/
        ├── spotify_client.py     # wrapper spotipy (refresh, top, playlist, cover)
        └── cover_generator.py    # portada + 12 paletas + preview
```
