"""Paleta del panel de backtest.

Estos tokens son la única fuente de color: `dashboard.py` los inyecta como
variables CSS y como objeto JS, de modo que el HTML y los gráficos de Plotly
comparten paleta sin duplicar hexadecimales.

Paleta validada para daltonismo (ΔE CVD 24.7 en el par categórico usado) sobre
la superficie clara `#fcfcfb`. El panel se genera en modo claro: es un fichero
local de análisis, no una página que herede el tema del visor.
"""

from __future__ import annotations

# --- Tokens de color --------------------------------------------------------

SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

# Categóricos, en orden fijo. Nunca se ciclan.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")

# Un tono más fuera de la serie categórica, para las capas que necesitan
# distinguir una tercera cosa sin repetir el azul, el naranja o el verde: las
# zonas del módulo de estructura llevan un color por temporalidad.
VIOLET = "#6f4bd8"

# Los tonos de lo que dibuja el PROPIETARIO a mano encima del gráfico y no ha
# pintado el motor: los tres recuadros con los que marca un PUL, un UL y un
# APUL. Quedan fuera de la serie categórica y del violeta de las temporalidades
# a propósito, para que no se confundan con ninguna capa calculada, y entre
# ellos van a hues separados (318°, 190° y 70°) para poder distinguirse.
MAGENTA = "#c0359a"
CYAN = "#0b8fa8"
OLIVE = "#7c8b12"

# Polaridad (ganancia / pérdida): pareja divergente azul ↔ rojo con gris neutro.
POSITIVE = "#2a78d6"
NEGATIVE = "#d03b3b"
NEUTRAL = "#f0efec"

# Escala divergente para el mapa de calor mensual.
DIVERGING_SCALE = [
    [0.0, "#8f2323"],
    [0.25, "#d03b3b"],
    [0.5, NEUTRAL],
    [0.75, "#3987e5"],
    [1.0, "#184f95"],
]

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'
