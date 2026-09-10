"""Qué días de mercado FALTAN en el histórico diario.

Un hueco en el histórico no da ningún error: da un rango CRT que se detecta
contra la vela equivocada. Si el martes no está, el miércoles se lee contra el
lunes y el rango sale de dos velas que en el mercado no fueron consecutivas.
Por eso esto se imprime ANTES de dibujar nada.

Qué días se esperan depende de dónde empieza el día. Con el corte en las 17:00
de Nueva York, la semana de mercado va del domingo a las 17:00 al viernes a las
17:00, así que hay sesión los días cuya apertura cae en domingo, lunes, martes,
miércoles y jueves —hora de la plaza, no UTC—, y no la hay en las aperturas de
viernes ni de sábado.

Los festivos salen aquí como días que faltan, y está bien que salgan: no hay
forma de distinguir un festivo de una descarga incompleta mirando sólo el
índice, y quien mira el informe sí sabe qué día fue Navidad.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd

#: Días de la semana —hora de la plaza— en los que NO abre una sesión. La semana
#: arranca el domingo por la tarde y cierra el viernes por la tarde, así que las
#: aperturas de viernes y de sábado no existen.
CLOSED_WEEKDAYS = (4, 5)  # viernes y sábado, convención de pandas (lunes = 0)


@dataclass(frozen=True, slots=True)
class DailyCoverage:
    """Sesiones que hay, sesiones que debería haber y las que faltan."""

    timezone: str
    opens_at: time
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    present: int
    expected: int
    #: Aperturas de sesión que el histórico no tiene, en UTC y en orden.
    missing: tuple[pd.Timestamp, ...]

    @property
    def ok(self) -> bool:
        return not self.missing

    def describe(self) -> str:
        if self.first is None:
            return "sin ninguna vela diaria"
        falta = (
            "sin días sueltos"
            if self.ok
            else f"faltan {len(self.missing)} sesiones (festivos incluidos)"
        )
        return (
            f"{self.present} sesiones de {self.expected} esperadas · {falta} · "
            f"el día abre a las {self.opens_at.hour:02d}:{self.opens_at.minute:02d} "
            f"de {self.timezone}"
        )

    def missing_by_year(self) -> dict[int, int]:
        """Cuántas sesiones faltan por año. Un año entero fuera se ve de un vistazo."""
        counts: dict[int, int] = {}
        for moment in self.missing:
            year = int(moment.year)
            counts[year] = counts.get(year, 0) + 1
        return counts


def daily_coverage(
    index: pd.DatetimeIndex, timezone: str = "UTC", opens_at: time = time(0, 0)
) -> DailyCoverage:
    """Compara las velas diarias que hay con las sesiones que debería haber.

    `index` son las marcas de las velas diarias ya agregadas: cada una es la
    apertura de su sesión. `timezone` y `opens_at` son el ancla con la que se
    agregaron —`America/New_York` y 17:00 en este proyecto—; con ellos se
    reconstruye el calendario de sesiones y se resta.
    """
    utc = _utc(index)
    if len(utc) == 0:
        return DailyCoverage(
            timezone=timezone,
            opens_at=opens_at,
            first=None,
            last=None,
            present=0,
            expected=0,
            missing=(),
        )

    local = utc.tz_convert(timezone)
    days = pd.DatetimeIndex(pd.Series(local).dt.normalize().unique()).tz_localize(None)
    calendar = pd.date_range(days[0], days[-1], freq="D")
    open_days = calendar[~calendar.weekday.isin(CLOSED_WEEKDAYS)]

    opening = pd.Timedelta(hours=opens_at.hour, minutes=opens_at.minute)
    expected = pd.DatetimeIndex(open_days + opening).tz_localize(timezone).tz_convert("UTC")
    missing = expected.difference(utc)

    return DailyCoverage(
        timezone=timezone,
        opens_at=opens_at,
        first=pd.Timestamp(utc[0]),
        last=pd.Timestamp(utc[-1]),
        present=len(utc),
        expected=len(expected),
        missing=tuple(pd.Timestamp(moment) for moment in missing),
    )


def _utc(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    stamps = pd.DatetimeIndex(index)
    return stamps.tz_localize("UTC") if stamps.tz is None else stamps.tz_convert("UTC")
