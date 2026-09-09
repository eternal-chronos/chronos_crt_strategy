"""Calendario de la corrida: rollovers de swap, días de trading y sesión abierta.

Se precalcula de forma vectorizada una sola vez; el bucle del motor solo lee
arrays booleanos, sin trabajo con fechas por barra.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from chronos.domain.instrument import InstrumentSpec


@dataclass(frozen=True, slots=True)
class BarFlags:
    """Banderas por barra, alineadas con la serie de precios."""

    is_rollover: np.ndarray  # bool: en esta barra se devenga el swap
    swap_weekday: np.ndarray  # int: día de la semana que se cierra en el rollover
    day_id: np.ndarray  # int: identificador del día de trading (servidor)
    session_open: np.ndarray  # bool: fuera del corte diario del bróker
    is_last_of_day: np.ndarray  # bool: última barra de cada día de trading

    def __len__(self) -> int:
        return len(self.is_rollover)


def build_bar_flags(index: pd.DatetimeIndex, spec: InstrumentSpec) -> BarFlags:
    """Deriva el calendario a partir de los timestamps y la ficha del símbolo."""
    if index.tz is None:
        index = index.tz_localize("UTC")
    server_time = index.tz_convert(spec.session.timezone)

    # El "día de trading" arranca en la hora de rollover del servidor.
    shifted = server_time - pd.Timedelta(hours=spec.swap.rollover_hour_server)
    day_key = shifted.normalize()
    # Días desde epoch. Se convierte a datetime64[D] en vez de dividir enteros:
    # la resolución interna de pandas puede ser ns o us según la versión.
    day_id = day_key.tz_localize(None).to_numpy(dtype="datetime64[D]").astype("int64")

    changed = np.empty(len(day_id), dtype=bool)
    changed[0] = False  # la primera barra no devenga swap: no hubo noche previa
    changed[1:] = day_id[1:] != day_id[:-1]

    # El swap del rollover corresponde al día que acaba de cerrarse.
    previous_day = np.roll(np.asarray(day_key.weekday, dtype=np.int64), 1)
    previous_day[0] = int(day_key.weekday[0])

    is_last_of_day = np.empty(len(day_id), dtype=bool)
    is_last_of_day[-1] = True
    is_last_of_day[:-1] = day_id[1:] != day_id[:-1]

    session_open = _session_mask(server_time, spec)

    return BarFlags(
        is_rollover=changed,
        swap_weekday=previous_day,
        day_id=np.asarray(day_id, dtype=np.int64),
        session_open=session_open,
        is_last_of_day=is_last_of_day,
    )


def _session_mask(server_time: pd.DatetimeIndex, spec: InstrumentSpec) -> np.ndarray:
    """`True` donde el mercado está abierto según el corte diario del bróker."""
    session = spec.session
    if session.break_start is None or session.break_end is None:
        return np.ones(len(server_time), dtype=bool)

    minutes = server_time.hour * 60 + server_time.minute
    start = session.break_start.hour * 60 + session.break_start.minute
    end = session.break_end.hour * 60 + session.break_end.minute

    if start <= end:
        in_break = (minutes >= start) & (minutes < end)
    else:  # el corte cruza medianoche
        in_break = (minutes >= start) | (minutes < end)
    return ~np.asarray(in_break, dtype=bool)
