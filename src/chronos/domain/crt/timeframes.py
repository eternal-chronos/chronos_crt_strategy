"""La etiqueta horaria de las velas H4: a qué hora de Nueva York abre cada una.

Las velas H4 de este proyecto no son las de una rejilla fija en UTC: nacen del
mismo corte de sesión que el diario —17:00 de Nueva York, con el horario de
verano resuelto de verdad— y se trocean cada cuatro horas dentro de ella. Eso
las deja abriendo a las 17:00, 21:00, 01:00, 05:00, 09:00 y 13:00 del reloj de
la plaza.

**Aquí no se resamplea nada.** El corte de sesión ya está escrito una vez, en el
agregador que produce las velas del explorador, y duplicarlo sería tener dos
implementaciones del mismo horario de verano esperando a divergir. Este módulo
pone lo único que faltaba: la etiqueta que dice a qué hora de la plaza abrió
cada vela, y el filtro que se apoya en ella.

La etiqueta es una hora de PARED, no un desplazamiento: en UTC la misma vela de
las 09:00 de Nueva York cae a las 13:00 o a las 14:00 según el mes, y quien
quiera hablar de «la vela de las nueve» no puede hacerlo en UTC.

Una advertencia sobre las seis horas: son las de una sesión normal. Los dos fines
de semana del año en que se mueve el reloj la sesión dura 23 o 25 horas y puede
aparecer un trozo suelto con otra apertura. No se rechaza: se etiqueta como lo
que es y el filtro decidirá.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import numpy as np
import pandas as pd

from chronos.domain.errors import DomainError

#: Columna con la hora de apertura de la vela H4 en el reloj de la plaza.
H4_OPEN_NY = "h4_open_ny"

#: La plaza cuyo reloj parte el día. Es la misma con la que se corta el diario.
SESSION_TIMEZONE = "America/New_York"

#: Las seis aperturas de una sesión normal que empieza a las 17:00 NY. Están
#: aquí para poder nombrarlas en la configuración y en los mensajes, no como
#: validación: la sesión del cambio de reloj puede dejar un trozo con otra hora.
SESSION_OPENS_NY: tuple[str, ...] = ("17:00", "21:00", "01:00", "05:00", "09:00", "13:00")

_OPEN = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


def h4_opens_ny(index: pd.DatetimeIndex, timezone: str = SESSION_TIMEZONE) -> np.ndarray:
    """Hora de apertura de cada vela, en el reloj de la plaza, como `HH:MM`."""
    stamps = pd.DatetimeIndex(index)
    if stamps.tz is None:
        raise DomainError("Las velas H4 exigen un índice tz-aware en UTC")
    return np.asarray(stamps.tz_convert(timezone).strftime("%H:%M"), dtype=object)


def with_h4_opens(bars: pd.DataFrame, timezone: str = SESSION_TIMEZONE) -> pd.DataFrame:
    """Las mismas velas H4 con la columna `h4_open_ny` añadida.

    No toca ni el índice ni el OHLC: las velas siguen etiquetadas al inicio de
    su intervalo, que es la convención de barras del proyecto.
    """
    frame = bars.copy()
    frame[H4_OPEN_NY] = h4_opens_ny(pd.DatetimeIndex(frame.index), timezone)
    return frame


def normalize_key_opens(values: Iterable[str] | None) -> tuple[str, ...] | None:
    """Valida y normaliza la lista de aperturas clave. `None` = no filtrar nada.

    Se admite `9:00` y se escribe `09:00`: la etiqueta viaja con dos cifras, y
    una hora sin el cero delante no casaría con nada y filtraría en silencio.
    """
    if values is None:
        return None
    opens = tuple(str(value).strip() for value in values)
    if not opens:
        raise DomainError(
            "key_h4_opens_ny está vacío: para no filtrar ninguna vela, pásalo como None"
        )
    normalized: list[str] = []
    for value in opens:
        match = _OPEN.match(value)
        if match is None:
            raise DomainError(
                f"Apertura H4 inválida: {value!r}. Se escribe HH:MM en hora de "
                f"{SESSION_TIMEZONE} ({', '.join(SESSION_OPENS_NY)})"
            )
        hour, minute = int(match.group("hour")), int(match.group("minute"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise DomainError(f"Apertura H4 fuera del reloj: {value!r}")
        normalized.append(f"{hour:02d}:{minute:02d}")
    return tuple(dict.fromkeys(normalized))


def key_open_mask(opens: Iterable[str], key_h4_opens_ny: Iterable[str] | None) -> np.ndarray:
    """Qué velas pasan el filtro horario. Sin filtro, todas.

    Devuelve una máscara alineada con `opens`, que son las etiquetas de
    `h4_open_ny` de las velas —o de las velas que confirmaron un rango—.
    """
    wanted = normalize_key_opens(key_h4_opens_ny)
    values = np.asarray(list(opens), dtype=object)
    if wanted is None:
        return np.ones(len(values), dtype=bool)
    return np.isin(values, np.asarray(wanted, dtype=object))


def describe_key_opens(key_h4_opens_ny: Iterable[str] | None) -> str:
    """Cómo se cuenta el filtro horario en pantalla."""
    wanted = normalize_key_opens(key_h4_opens_ny)
    if wanted is None:
        return "sin filtro horario: valen las seis aperturas de la sesión"
    return "sólo las velas que abren a las " + ", ".join(wanted) + f" de {SESSION_TIMEZONE}"
