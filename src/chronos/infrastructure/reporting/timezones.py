"""Cómo se escribe la zona de la sesión al lado de una hora.

`Etc/GMT+4` es la zona IANA del UTC-4 fijo, el reloj con el que cTrader dibuja.
Su nombre lleva el signo al revés —convención POSIX, heredada de la variable
`TZ`— así que impreso junto a una hora se lee como lo contrario de lo que es. En
un gráfico cuya razón de ser es auditar horas eso no es cosmético: se escribe
`UTC-4`.

Las zonas con nombre de plaza (`America/New_York`, `Europe/Athens`) se escriben
tal cual: dicen dónde está el reloj, que es más información que su desfase.
"""

from __future__ import annotations

import re

_ETC_OFFSET = re.compile(r"^Etc/GMT(?P<sign>[+-])(?P<hours>\d{1,2})$")


def session_label(timezone: str) -> str:
    """Nombre con el que se imprime la zona. Sólo cambia las `Etc/GMT±N`."""
    match = _ETC_OFFSET.match(timezone)
    if match is None:
        return timezone
    sign = "-" if match.group("sign") == "+" else "+"
    return f"UTC{sign}{int(match.group('hours'))}"
