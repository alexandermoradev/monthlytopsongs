"""
cover_generator.py — Generación de la portada de la playlist.

Crea una imagen cuadrada con un gradiente "soft" (replicando la estructura de
assets/../imagen-ejemplo.png) y tres textos centrados en blanco:
  - arriba (pequeño):  texto fijo (por defecto "alex")
  - centro (grande):   mes en minúsculas
  - debajo (grande):   año

ESTRUCTURA DEL GRADIENTE (medida sobre la referencia):
  - Valor (brillo) altísimo y casi constante  -> aspecto luminoso.
  - Saturación BAJA, concentrada en una banda central que se desvanece a casi
    blanco hacia los bordes (saturación radial).
  - El TONO rota suavemente en vertical (arriba un color, abajo otro análogo).

Las 12 portadas (una por mes) rotan el tono base +30°/mes recorriendo el
círculo cromático; enero reproduce el rosa→melocotón de la referencia.

Ejecutable para regenerar previews locales sin Django:
    .venv/bin/python wrap/services/cover_generator.py            # preview de enero
    .venv/bin/python wrap/services/cover_generator.py montaje    # rejilla de los 12
"""

import base64
import io
import logging
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger("wrap.cover")

# Rutas (derivadas de la ubicación de este fichero, sin depender de Django).
BASE_DIR = Path(__file__).resolve().parents[2]
RUTA_FUENTE = BASE_DIR / "assets" / "fonts" / "Inter-Variable.ttf"

# Nombres de mes en inglés y minúsculas (índice 0 = enero), independiente del locale.
MESES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

# ---------------------------------------------------------------------------
# GRADIENTE (parámetros calcados de imagen-ejemplo.png)
# ---------------------------------------------------------------------------
HUE_TOP_ENERO = 333    # tono superior de enero (rosa/magenta)
PASO_HUE = 30          # el tono base avanza 30° por mes (12 meses = 360°)
SPAN_HUE = 57          # rotación de tono de ARRIBA a ABAJO dentro de una portada
                       # (enero: 333 -> 390=30, rosa arriba -> melocotón abajo)

VALOR = 1.0            # brillo máximo (muy luminoso, como la referencia)
# Para que el texto BLANCO se lea en TODOS los meses, bajamos el brillo solo en
# la zona con color (el centro, donde va el texto) hasta una luminancia objetivo;
# los bordes blancos se conservan. Calibrado al contraste del rosa de la referencia.
L_OBJETIVO = 0.72
SAT_PICO = 0.32        # saturación máxima (centro de la banda)
SAT_BORDE = 0.04       # saturación en los bordes (casi blanco)
SAT_CENTRO_XY = (0.50, 0.40)  # dónde es máxima la saturación
SAT_SIGMA_X = 0.33     # ancho horizontal de la banda saturada (estrecho -> bordes blancos)
SAT_SIGMA_Y = 0.62     # ancho vertical (amplio -> color de arriba a abajo)

# Textos: tamaño (fracción del lado), separaciones y centro vertical del bloque.
TOP_TEXT_SIZE_FRAC = 0.052
BIG_TEXT_SIZE_FRAC = 0.170
GAP_TOP_MES_FRAC = 0.018   # separación alex -> mes (juntos)
GAP_MES_ANIO_FRAC = 0.004  # separación mes -> año (muy juntos)
BLOQUE_CENTRO_Y_FRAC = 0.50  # el bloque de texto se centra aquí

COLOR_TEXTO = (255, 255, 255)     # blanco puro, sin sombra

# Límite de Spotify para PUT /playlists/{id}/images (la cadena base64 ≤ 256 KB).
LIMITE_BASE64 = 256 * 1024


def _hsv_to_rgb_np(h, s, v):
    """Conversión HSV->RGB vectorizada (h,s,v en [0,1]; devuelve array ...x3 en [0,1])."""
    i = np.floor(h * 6.0).astype(int)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i = i % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def hue_top_mes(mes_num):
    """Tono superior (grados) del mes 1..12."""
    return (HUE_TOP_ENERO + (mes_num - 1) * PASO_HUE) % 360


def _cargar_fuente(tam_px):
    """Carga Inter en peso Black al tamaño dado."""
    fuente = ImageFont.truetype(str(RUTA_FUENTE), tam_px)
    try:
        fuente.set_variation_by_name("Black")  # fuente variable -> instancia Black (900)
    except Exception:  # noqa: BLE001
        pass
    return fuente


def _generar_fondo(tam, mes_num):
    """Construye el gradiente del mes como imagen PIL replicando la referencia."""
    yy, xx = np.mgrid[0:tam, 0:tam].astype(np.float32)
    ny = yy / (tam - 1)
    nx = xx / (tam - 1)

    # Tono: rota en vertical desde el tono superior del mes hasta +SPAN_HUE.
    hue_deg = (hue_top_mes(mes_num) + SPAN_HUE * ny) % 360.0
    h = hue_deg / 360.0

    # Saturación: pico en el centro (banda), se desvanece radialmente a los bordes.
    cx, cy = SAT_CENTRO_XY
    gx = np.exp(-(((nx - cx) / SAT_SIGMA_X) ** 2))
    gy = np.exp(-(((ny - cy) / SAT_SIGMA_Y) ** 2))
    s = SAT_BORDE + (SAT_PICO - SAT_BORDE) * gx * gy

    # Compensación de luminancia: el amarillo/verde son intrínsecamente claros,
    # así que a igual saturación quedarían casi blancos (texto ilegible). Calculamos
    # qué brillo necesita CADA tono para alcanzar L_OBJETIVO, y lo aplicamos solo
    # donde hay color (proporcional a la saturación). Los bordes blancos no se tocan.
    rgb1 = _hsv_to_rgb_np(h, s, np.ones_like(s))  # color a brillo máximo
    lum = 0.2126 * rgb1[..., 0] + 0.7152 * rgb1[..., 1] + 0.0722 * rgb1[..., 2]
    val_comp = np.minimum(VALOR, L_OBJETIVO / np.maximum(lum, 1e-6))
    s_norm = np.clip((s - SAT_BORDE) / (SAT_PICO - SAT_BORDE), 0.0, 1.0)
    v = VALOR - (VALOR - val_comp) * s_norm  # bordes -> VALOR; centro -> val_comp

    rgb = _hsv_to_rgb_np(h, s, v)
    arr = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    fondo = Image.fromarray(arr, mode="RGB")

    # Difuminado muy leve: garantiza "soft, sin bandas".
    return fondo.filter(ImageFilter.GaussianBlur(radius=tam / 220))


def generar_portada(mes_num, anio, texto_top="alex", tam=640, supersample=2):
    """Genera la portada (Image PIL) de `tam`x`tam` px para el mes `mes_num` (1..12)."""
    grande = tam * supersample
    img = _generar_fondo(grande, mes_num)
    draw = ImageDraw.Draw(img)

    fuente_top = _cargar_fuente(int(TOP_TEXT_SIZE_FRAC * grande))
    fuente_big = _cargar_fuente(int(BIG_TEXT_SIZE_FRAC * grande))

    mes_txt = MESES[mes_num - 1]
    anio_txt = str(anio)

    def _alto(texto, fuente):
        l, t, r, b = draw.textbbox((0, 0), texto, font=fuente)
        return b - t

    h_top = _alto(texto_top, fuente_top)
    h_mes = _alto(mes_txt, fuente_big)
    h_anio = _alto(anio_txt, fuente_big)

    gap1 = GAP_TOP_MES_FRAC * grande
    gap2 = GAP_MES_ANIO_FRAC * grande
    total = h_top + gap1 + h_mes + gap2 + h_anio

    arriba = BLOQUE_CENTRO_Y_FRAC * grande - total / 2
    cx = grande / 2
    y_top = arriba + h_top / 2
    y_mes = arriba + h_top + gap1 + h_mes / 2
    y_anio = arriba + h_top + gap1 + h_mes + gap2 + h_anio / 2

    draw.text((cx, y_top), texto_top, font=fuente_top, fill=COLOR_TEXTO, anchor="mm")
    draw.text((cx, y_mes), mes_txt, font=fuente_big, fill=COLOR_TEXTO, anchor="mm")
    draw.text((cx, y_anio), anio_txt, font=fuente_big, fill=COLOR_TEXTO, anchor="mm")

    if supersample != 1:
        img = img.resize((tam, tam), Image.LANCZOS)
    return img


def generar_base64(mes_num, anio, texto_top="alex", tam=640):
    """Genera la portada y la devuelve como (base64_str, bytes_jpeg)."""
    img = generar_portada(mes_num, anio, texto_top=texto_top, tam=tam)
    datos, b64, calidad = None, None, None
    for calidad in range(92, 40, -6):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=calidad, optimize=True)
        datos = buf.getvalue()
        b64 = base64.b64encode(datos)
        if len(b64) <= LIMITE_BASE64:
            break
    logger.info(
        "Portada generada: %d bytes JPEG (base64=%d B, calidad=%d).",
        len(datos), len(b64), calidad,
    )
    return b64.decode("ascii"), len(datos)


def guardar_preview(mes_num=1, anio="2026", ruta=None):
    """Guarda un preview.jpg en la raíz del proyecto para validación visual."""
    ruta = Path(ruta) if ruta else (BASE_DIR / "preview.jpg")
    generar_portada(mes_num, anio).save(ruta, format="JPEG", quality=92, optimize=True)
    print(f"Preview guardado en: {ruta}")
    return ruta


def generar_montaje(anio="2026", tam_celda=320, cols=3, margen=18, ruta=None):
    """Genera una rejilla con las 12 portadas (una por mes) para validar de un vistazo."""
    filas = (12 + cols - 1) // cols
    ancho = cols * tam_celda + (cols + 1) * margen
    alto = filas * tam_celda + (filas + 1) * margen
    lienzo = Image.new("RGB", (ancho, alto), (244, 244, 246))
    for i in range(12):
        cov = generar_portada(i + 1, anio, tam=tam_celda)
        fila, col = divmod(i, cols)
        x = margen + col * (tam_celda + margen)
        y = margen + fila * (tam_celda + margen)
        lienzo.paste(cov, (x, y))
    ruta = Path(ruta) if ruta else (BASE_DIR / "previews_12_meses.jpg")
    lienzo.save(ruta, format="JPEG", quality=90, optimize=True)
    print(f"Montaje guardado en: {ruta}")
    return ruta


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "montaje":
        generar_montaje()
    else:
        guardar_preview()
