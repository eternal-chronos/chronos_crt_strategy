"""Verificación empírica de la zona horaria del histórico (§1.1).

Obligatoria **antes de calcular nada**. Un offset horario equivocado no produce
ningún error visible: produce velas H4 desplazadas y, por tanto, impulsos
distintos a los que el propietario ve en su pantalla. Se comprueban tres hechos
que no dependen de ninguna convención del bróker:

- **A.1** el hueco semanal debe caer sábado y domingo UTC;
- **A.2** el rango medio por minuto del día debe tener un pico marcado hacia las
  13:30 UTC, cuando salen los datos macro de EE. UU.;
- **A.3** ese pico debe **moverse una hora** con el horario de verano de EE. UU.:
  las 8:30 de Nueva York son las 13:30 UTC en invierno y las 12:30 en verano.

A.1 y A.2 son bloqueantes. A.3 es diagnóstico: no puede fallar por sí sola sin
que fallen también las otras dos, y con pocos años de histórico su lectura es
frágil, así que se reporta y no detiene la fase.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.chart.config import TimezoneAuditConfig

WEEKDAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

#: Zona de la que dependen los datos macro que hacen el pico: las 8:30 de Nueva
#: York. En horario de verano (EDT, UTC-4) caen una hora antes en UTC.
RELEASE_TIMEZONE = "America/New_York"
DST_OFFSET = pd.Timedelta(hours=-4)
#: Fines de semana que se muestran uno a uno para la comprobación manual (A.1).
SAMPLE_WEEKENDS = 10


@dataclass(frozen=True, slots=True)
class WeekendGap:
    """Parada larga del mercado: última barra antes y primera barra después."""

    last_before: datetime
    first_after: datetime
    hours: float

    @property
    def start_weekday(self) -> int:
        return int(self.last_before.weekday())

    @property
    def end_weekday(self) -> int:
        return int(self.first_after.weekday())


@dataclass(frozen=True, slots=True)
class ProfilePeak:
    """Pico del perfil de volatilidad de un tramo del histórico (A.2, A.3)."""

    #: Tramo medido: "2019", "2019 · verano EE. UU. (EDT)".
    label: str
    bars: int
    peak_minute_utc: str
    peak_mean_range: float
    #: Minuto en el que debía caer el pico según el régimen horario del tramo.
    expected_minute_utc: str
    expected_mean_range: float
    #: Puesto del minuto esperado en el perfil (1 = es el máximo). Un año donde
    #: el esperado queda segundo por milésimas no es lo mismo que uno donde ni
    #: aparece, y el máximo a secas no distingue esos dos casos.
    expected_rank: int

    @property
    def peak_minute_of_day(self) -> int:
        return _parse_minute(self.peak_minute_utc)

    @property
    def offset_minutes(self) -> int:
        return _circular_minute_distance(
            self.peak_minute_of_day, _parse_minute(self.expected_minute_utc)
        )


@dataclass(frozen=True, slots=True)
class SeasonPeaks:
    """Los dos picos de un año, uno por régimen horario de EE. UU. (A.3).

    Con la zona horaria bien puesta, el pico de verano cae **una hora antes** en
    UTC que el de invierno. Que no se mueva —o que se mueva al revés— es la
    firma de un histórico desplazado.
    """

    year: int
    #: Tramo medido: un año o el histórico entero.
    label: str
    whole: ProfilePeak | None
    #: Verano de EE. UU. (EDT, UTC-4): las 8:30 de Nueva York son las 12:30 UTC.
    daylight: ProfilePeak | None
    #: Invierno (EST, UTC-5): las 8:30 de Nueva York son las 13:30 UTC.
    standard: ProfilePeak | None

    @property
    def shift_minutes(self) -> int | None:
        """Minutos que el invierno va por detrás del verano. Se espera 60."""
        if self.daylight is None or self.standard is None:
            return None
        return self.standard.peak_minute_of_day - self.daylight.peak_minute_of_day


@dataclass(frozen=True, slots=True)
class TimezoneAudit:
    """Resultado de la verificación. `ok=False` obliga a parar la fase 1."""

    ok: bool
    problems: tuple[str, ...]
    bars: int
    first_bar: datetime
    last_bar: datetime

    gaps_found: int
    gaps_starting_friday: int
    gaps_ending_sunday: int
    gap_start_weekday_counts: dict[str, int]
    gap_end_weekday_counts: dict[str, int]
    sample_gaps: tuple[WeekendGap, ...]

    peak_minute_utc: str
    peak_mean_range: float
    expected_peak_utc: str
    tolerance_minutes: int
    #: Tolerancia realmente aplicada. Con barras de una hora el pico sólo puede
    #: caer en horas en punto, así que exigirle la precisión del minuto sería
    #: exigirle algo que el dato no puede dar.
    effective_tolerance_minutes: int
    resolution_minutes: int
    peak_offset_minutes: int
    top_minutes: tuple[tuple[str, float], ...]
    #: Perfil completo (1440 valores) del rango medio por minuto del día.
    minute_profile: tuple[float, ...]
    #: Rango medio por hora UTC (24 valores). Es la tabla que pide A.2.
    hour_profile: tuple[float, ...]
    #: Un fin de semana por año, para la comprobación manual de A.1.
    yearly_gaps: tuple[WeekendGap, ...]
    #: Perfil por año y por régimen horario de EE. UU. (A.3).
    seasons: tuple[SeasonPeaks, ...]
    #: Los dos regímenes sobre el histórico entero: la medición limpia de A.3.
    #: Un año suelto puede tener el pico esperado en segundo puesto por milésimas.
    whole_history_seasons: SeasonPeaks | None

    @property
    def peak_hour_utc(self) -> str:
        """Hora UTC con el rango medio más alto. Independiente del minuto."""
        if not self.hour_profile:
            return "n/d"
        return f"{int(np.nanargmax(np.asarray(self.hour_profile))):02d}:00"

    @property
    def dst_shift_minutes(self) -> int | None:
        """Minutos que el pico de invierno va por detrás del de verano (A.3)."""
        return self.whole_history_seasons.shift_minutes if self.whole_history_seasons else None

    @property
    def dst_shift_ok(self) -> bool | None:
        """`True` si el pico se desplaza exactamente una hora con el verano.

        `None` cuando el histórico no cubre los dos regímenes y no hay nada que
        medir. Es diagnóstico, no bloqueante: A.1 y A.2 son las que detienen la
        fase, y un histórico corto no tiene la culpa de no cubrir dos estaciones.
        """
        shift = self.dst_shift_minutes
        return None if shift is None else shift == 60


def audit_timezone(frame: pd.DataFrame, config: TimezoneAuditConfig) -> TimezoneAudit:
    """Ejecuta las dos comprobaciones de §1.1 sobre las barras M1 en UTC."""
    if frame.empty:
        raise ValueError("No hay barras que auditar")
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        raise ValueError("El histórico debe ser tz-aware para poder auditarlo")
    index = index.tz_convert("UTC")

    problems: list[str] = []
    gaps = _weekend_gaps(index, config.weekend_gap_hours)
    start_counts = _weekday_counts(gap.start_weekday for gap in gaps)
    end_counts = _weekday_counts(gap.end_weekday for gap in gaps)
    friday = sum(1 for gap in gaps if gap.start_weekday == 4)
    sunday = sum(1 for gap in gaps if gap.end_weekday == 6)

    if not gaps:
        problems.append(
            f"No se encontró ninguna parada de más de {config.weekend_gap_hours:.0f} h: "
            "el histórico no parece contener fines de semana"
        )
    else:
        if friday / len(gaps) < config.min_conformity:
            problems.append(
                f"Sólo {friday}/{len(gaps)} paradas empiezan en viernes UTC "
                f"(mínimo exigido {config.min_conformity:.0%}): el histórico no está en UTC"
            )
        if sunday / len(gaps) < config.min_conformity:
            problems.append(
                f"Sólo {sunday}/{len(gaps)} paradas terminan en domingo UTC "
                f"(mínimo exigido {config.min_conformity:.0%}): el histórico no está en UTC"
            )

    profile = _minute_profile(frame, index)
    peak_minute = int(np.nanargmax(profile))
    standard_minute = _parse_minute(config.expected_peak_utc)
    daylight_minute = _daylight_minute(standard_minute)
    # El dato macro sale a una hora de Nueva York, no de Londres: en UTC cae a
    # las 13:30 en invierno y a las 12:30 en verano. El agregado del histórico
    # entero se lleva al régimen con más meses, así que se admite cualquiera de
    # los dos y es A.3 quien comprueba que ambos están en su sitio.
    offset = min(
        _circular_minute_distance(peak_minute, standard_minute),
        _circular_minute_distance(peak_minute, daylight_minute),
    )
    resolution = _resolution_minutes(index)
    # Con barras horarias el pico sólo puede caer en horas en punto: pedirle
    # precisión de minuto sería dar por malo un histórico correcto.
    tolerance = max(config.tolerance_minutes, resolution // 2)
    if offset > tolerance:
        problems.append(
            f"El pico de volatilidad está en {_format_minute(peak_minute)} UTC y se esperaba "
            f"{config.expected_peak_utc} (invierno EE. UU.) o "
            f"{_format_minute(daylight_minute)} (verano) ±{tolerance} min "
            f"(desviación {offset} min): revisa el offset horario del histórico"
        )

    order = np.argsort(np.nan_to_num(profile, nan=-np.inf))[::-1][:5]
    ranges = frame["high"].to_numpy(dtype=float) - frame["low"].to_numpy(dtype=float)
    return TimezoneAudit(
        ok=not problems,
        problems=tuple(problems),
        bars=len(index),
        first_bar=index[0].to_pydatetime(),
        last_bar=index[-1].to_pydatetime(),
        gaps_found=len(gaps),
        gaps_starting_friday=friday,
        gaps_ending_sunday=sunday,
        gap_start_weekday_counts=start_counts,
        gap_end_weekday_counts=end_counts,
        sample_gaps=tuple(gaps[:5]),
        peak_minute_utc=_format_minute(peak_minute),
        peak_mean_range=float(profile[peak_minute]),
        expected_peak_utc=config.expected_peak_utc,
        tolerance_minutes=config.tolerance_minutes,
        effective_tolerance_minutes=tolerance,
        resolution_minutes=resolution,
        peak_offset_minutes=offset,
        top_minutes=tuple((_format_minute(int(m)), float(profile[int(m)])) for m in order),
        minute_profile=tuple(float(value) for value in profile),
        hour_profile=tuple(_hour_profile(index, ranges)),
        yearly_gaps=tuple(_one_gap_per_year(gaps)),
        seasons=tuple(_seasons(index, ranges, standard_minute)),
        whole_history_seasons=_whole_history_seasons(index, ranges, standard_minute),
    )


def _weekend_gaps(index: pd.DatetimeIndex, threshold_hours: float) -> list[WeekendGap]:
    if len(index) < 2:
        return []
    deltas = index.to_series().diff()
    mask = deltas > pd.Timedelta(hours=threshold_hours)
    positions = [int(position) for position in np.flatnonzero(mask.to_numpy()) if position > 0]
    return [
        WeekendGap(
            last_before=index[position - 1].to_pydatetime(),
            first_after=index[position].to_pydatetime(),
            hours=float((index[position] - index[position - 1]).total_seconds() / 3600.0),
        )
        for position in positions
    ]


def _weekday_counts(weekdays: Iterable[int]) -> dict[str, int]:
    counts = dict.fromkeys(WEEKDAY_NAMES, 0)
    for weekday in weekdays:
        counts[WEEKDAY_NAMES[int(weekday)]] += 1
    return counts


def _minute_profile(frame: pd.DataFrame, index: pd.DatetimeIndex) -> np.ndarray:
    """Rango medio (high - low) por minuto del día, agregado sobre todo el histórico."""
    minute_of_day = (index.hour * 60 + index.minute).to_numpy()
    ranges = frame["high"].to_numpy(dtype=float) - frame["low"].to_numpy(dtype=float)
    return _mean_by(minute_of_day, ranges, size=1440)


def _one_gap_per_year(gaps: list[WeekendGap]) -> list[WeekendGap]:
    """Una parada por año y, si sobran huecos, hasta diez repartidas (A.1).

    Un año por muestra y no diez seguidas del mismo tramo: lo que se comprueba a
    mano es que el hueco cae en sábado y domingo *en todo el histórico*, no en
    una semana concreta.
    """
    if not gaps:
        return []
    chosen: dict[int, WeekendGap] = {}
    for gap in gaps:
        chosen.setdefault(gap.last_before.year, gap)
    sample = sorted(chosen.values(), key=lambda gap: gap.last_before)
    if len(sample) >= SAMPLE_WEEKENDS:
        return sample[:SAMPLE_WEEKENDS]

    taken = {gap.last_before for gap in sample}
    remaining = [gap for gap in gaps if gap.last_before not in taken]
    missing = SAMPLE_WEEKENDS - len(sample)
    if remaining:
        # Repartidas por el histórico, no las primeras que caigan.
        step = max(1, len(remaining) // missing)
        sample += remaining[::step][:missing]
    return sorted(sample, key=lambda gap: gap.last_before)


def _hour_profile(index: pd.DatetimeIndex, ranges: np.ndarray) -> np.ndarray:
    """Rango medio (high - low) por hora UTC, agregado sobre todo el histórico."""
    return _mean_by(index.hour.to_numpy(), ranges, size=24)


def _seasons(
    index: pd.DatetimeIndex, ranges: np.ndarray, standard_minute: int
) -> list[SeasonPeaks]:
    """Perfil por año y por régimen horario de EE. UU. (A.3)."""
    years = index.year.to_numpy()
    return [
        _seasons_of(f"{year}", index, ranges, standard_minute, years == year)
        for year in sorted({int(value) for value in years})
    ]


def _whole_history_seasons(
    index: pd.DatetimeIndex, ranges: np.ndarray, standard_minute: int
) -> SeasonPeaks | None:
    if len(index) == 0:
        return None
    return _seasons_of(
        "todo el histórico", index, ranges, standard_minute, np.ones(len(index), dtype=bool)
    )


def _seasons_of(
    label: str,
    index: pd.DatetimeIndex,
    ranges: np.ndarray,
    standard_minute: int,
    selection: np.ndarray,
) -> SeasonPeaks:
    """Los dos regímenes horarios de un tramo.

    El régimen sale de la propia zona `America/New_York`, no de fechas escritas
    a mano: las reglas del cambio de hora se han movido más de una vez.
    """
    minute_of_day = (index.hour * 60 + index.minute).to_numpy()
    daylight = _is_daylight_saving(index)
    year = int(index.year.to_numpy()[selection][0]) if selection.any() else 0
    return SeasonPeaks(
        year=year,
        label=label,
        whole=_peak(label, minute_of_day[selection], ranges[selection], standard_minute),
        daylight=_peak(
            f"{label} · verano EE. UU. (EDT)",
            minute_of_day[selection & daylight],
            ranges[selection & daylight],
            _daylight_minute(standard_minute),
        ),
        standard=_peak(
            f"{label} · invierno EE. UU. (EST)",
            minute_of_day[selection & ~daylight],
            ranges[selection & ~daylight],
            standard_minute,
        ),
    )


def _is_daylight_saving(index: pd.DatetimeIndex) -> np.ndarray:
    """Máscara de las barras que caen en horario de verano de EE. UU."""
    local = index.tz_convert(RELEASE_TIMEZONE)
    offsets = local.tz_localize(None) - index.tz_localize(None)
    return np.asarray(offsets == DST_OFFSET)


def _daylight_minute(standard_minute: int) -> int:
    """El mismo dato macro, una hora antes en UTC durante el verano de EE. UU."""
    return (standard_minute - 60) % 1440


def _peak(
    label: str, minute_of_day: np.ndarray, ranges: np.ndarray, expected_minute: int
) -> ProfilePeak | None:
    if minute_of_day.size == 0:
        return None
    profile = _mean_by(minute_of_day, ranges, size=1440)
    if not np.isfinite(profile).any():
        return None
    filled = np.nan_to_num(profile, nan=-np.inf)
    peak = int(np.argmax(filled))
    rank = int((filled > filled[expected_minute]).sum()) + 1
    return ProfilePeak(
        label=label,
        bars=int(minute_of_day.size),
        peak_minute_utc=_format_minute(peak),
        peak_mean_range=float(profile[peak]),
        expected_minute_utc=_format_minute(expected_minute),
        expected_mean_range=float(profile[expected_minute]),
        expected_rank=rank,
    )


def _mean_by(buckets: np.ndarray, values: np.ndarray, *, size: int) -> np.ndarray:
    totals = np.zeros(size, dtype=float)
    counts = np.zeros(size, dtype=float)
    np.add.at(totals, buckets, values)
    np.add.at(counts, buckets, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, totals / counts, np.nan)


def _resolution_minutes(index: pd.DatetimeIndex) -> int:
    """Paso nativo del histórico en minutos, deducido del salto más frecuente."""
    if len(index) < 2:
        return 1
    deltas = index.to_series().diff().dropna()
    if deltas.empty:
        return 1
    step = pd.Timedelta(deltas.mode().iloc[0])
    return max(1, round(step.total_seconds() / 60))


def _parse_minute(value: str) -> int:
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def _format_minute(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _circular_minute_distance(left: int, right: int) -> int:
    """Distancia en minutos sobre el reloj de 24 h (23:50 y 00:05 distan 15)."""
    raw = abs(left - right)
    return int(min(raw, 1440 - raw))
