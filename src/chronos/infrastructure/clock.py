"""Adaptadores del puerto `Clock`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SystemClock:
    """Reloj real: la hora de la máquina, en UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Reloj simulado: devuelve el instante que se le dio y avanza a mano.

    Hace deterministas los artefactos con marca de tiempo (informes, ids de
    corrida) y permite mover el «ahora» de un bucle de paper/live en tests.
    """

    def __init__(self, moment: datetime) -> None:
        self._moment = _as_utc(moment)

    def now(self) -> datetime:
        return self._moment

    def advance(self, delta: timedelta) -> datetime:
        self._moment += delta
        return self._moment

    def set(self, moment: datetime) -> None:
        self._moment = _as_utc(moment)


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
