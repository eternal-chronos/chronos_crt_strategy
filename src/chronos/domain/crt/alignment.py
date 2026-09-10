"""La alineación de H4 con el sesgo diario: cuándo el precio está enfocado.

El sesgo diario dice HACIA DÓNDE va el precio. H4 dice CUÁNDO va enfocado hacia
ahí. Junta las dos cosas y salen cuatro estados, evaluados al cierre de cada
vela H4:

    SIN_TARGET        no hay ningún rango diario vivo: no hay nada hacia lo que ir
    TARGET_DEFINIDO   hay sesgo diario y ningún rango H4 vivo todavía: esperando
    H4_ALINEADO       el rango H4 que manda va en la misma dirección que el sesgo
    H4_EN_CONTRA      el rango H4 que manda va al revés: bloqueado

Aquí no se detecta ni un rango: los dos ciclos de vida —el del diario y el de
H4— los resuelve `ranges.detect_ranges`, que es la misma función para las dos
temporalidades. Esto sólo los cruza.

**Cuándo se supo cada cosa.** Es lo único delicado del módulo. Las velas van
etiquetadas al INICIO de su intervalo, así que la etiqueta de un rango no dice
cuándo el motor pudo conocerlo. Comparar la etiqueta de un rango diario contra
el cierre de una vela H4 sería mirar al futuro: la vela diaria que abre a las
17:00 lleva etiqueta anterior a los cierres H4 de las 21:00, la 1:00 y las 5:00,
y en ninguno de ellos había cerrado. Por eso los dos rangos entran aquí con sus
CIERRES ya resueltos —`t_confirm_close` y `t_resolved_close`, que los añade la
capa que conoce la rejilla de sesión— y todas las comparaciones se hacen contra
ellos.

**Qué rango manda.** El mismo criterio que el sesgo diario: el rango vivo MÁS
RECIENTE. Puede haber varios vivos a la vez y en direcciones opuestas; manda el
último porque es el que acaba de reescribir hacia dónde mira el mercado. Cuando
ese muere, vuelve a mandar el anterior si sigue vivo, y por eso esto no se
resuelve con una simple búsqueda del último confirmado.

`alignment_mode` elige cuánto se exige:

    latest   basta con que el rango H4 vivo más reciente vaya a favor
    strict   además, que NO quede ninguno vivo en contra
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

from chronos.domain.crt.ranges import BEARISH, BULLISH, COMPLETED
from chronos.domain.crt.timeframes import H4_OPEN_NY, key_open_mask
from chronos.domain.errors import DomainError

#: Los cuatro estados del sistema.
SIN_TARGET = "SIN_TARGET"
TARGET_DEFINIDO = "TARGET_DEFINIDO"
H4_ALINEADO = "H4_ALINEADO"
H4_EN_CONTRA = "H4_EN_CONTRA"

STATES: tuple[str, ...] = (SIN_TARGET, TARGET_DEFINIDO, H4_ALINEADO, H4_EN_CONTRA)

#: Cuánto se exige para dar por alineado.
LATEST = "latest"
STRICT = "strict"
MODES: tuple[str, ...] = (LATEST, STRICT)

#: Las dos marcas que este módulo exige y que el detector no puede poner: el
#: instante en que CERRÓ la vela que confirmó el rango y la que lo resolvió.
KNOWN_AT = "t_confirm_close"
RESOLVED_AT = "t_resolved_close"

#: Marca de los rangos H4 que existen pero no cuentan para la alineación porque
#: su vela de confirmación no abre a una de las horas clave.
IGNORED = "ignored_by_time_filter"

_DTYPES: dict[str, Any] = {
    "state": "object",
    "bias_d1": "object",
    "d1_target": "float64",
    "d1_range_id": "Int64",
    "h4_focus_range_id": "Int64",
    "h4_blocking_range_id": "Int64",
}

COLUMNS: tuple[str, ...] = tuple(_DTYPES)

#: Nombre del índice del resultado. Es el CIERRE de la vela H4, no su etiqueta.
INDEX_NAME = "h4_close"

#: Lo que se usa como «nunca se resolvió» al comparar instantes en enteros.
_NEVER = np.iinfo(np.int64).max


def compute_state_h4(
    ranges_d1: pd.DataFrame,
    ranges_h4: pd.DataFrame,
    h4_index: pd.DatetimeIndex,
    alignment_mode: str = LATEST,
    key_h4_opens_ny: Iterable[str] | None = None,
) -> pd.DataFrame:
    """El estado del sistema al cierre de cada vela H4.

    `h4_index` son los CIERRES de las velas H4, en orden. Los dos DataFrames de
    rangos son los que devuelve `detect_ranges` con `t_confirm_close` y
    `t_resolved_close` añadidos; `ranges_h4` necesita además `h4_open_ny` si se
    pasa un filtro horario.

    Devuelve un DataFrame indexado por esos cierres con las columnas de
    `COLUMNS`. Ni una fila mira más allá de su propio cierre.
    """
    mode = _mode(alignment_mode)
    clock = _clock(h4_index)

    d1 = _RangeArrays.of(ranges_d1)
    h4 = _RangeArrays.of(mark_time_filter(ranges_h4, key_h4_opens_ny))

    bias_at = _latest_alive(d1.known, d1.resolved, d1.order, clock)
    valid = np.flatnonzero(~h4.ignored)
    focus_at = _latest_alive_within(h4, valid, clock)
    bull_at = _latest_alive_within(h4, valid[h4.bullish[valid]], clock)
    bear_at = _latest_alive_within(h4, valid[~h4.bullish[valid]], clock)

    has_bias = bias_at >= 0
    bias = _pick(d1.direction, bias_at, has_bias)
    # El rango H4 vivo más reciente que va CONTRA el sesgo, lo mande o no. En
    # modo `latest` no se mira; en `strict` es justo lo que bloquea.
    contrary_at = np.where(bias == BULLISH, bear_at, np.where(bias == BEARISH, bull_at, -1))

    has_focus = has_bias & (focus_at >= 0)
    aligned = has_focus & (_pick(h4.direction, focus_at, has_focus) == bias)
    blocked_by_strict = has_bias & (mode == STRICT) & (contrary_at >= 0)

    state = np.where(
        ~has_bias,
        SIN_TARGET,
        np.where(
            focus_at < 0,
            TARGET_DEFINIDO,
            np.where(blocked_by_strict | ~aligned, H4_EN_CONTRA, H4_ALINEADO),
        ),
    )
    focus = np.where(state == H4_ALINEADO, focus_at, -1)
    blocking = np.where(
        state == H4_EN_CONTRA, np.where(blocked_by_strict, contrary_at, focus_at), -1
    )

    frame = pd.DataFrame(
        {
            "state": state.astype(object),
            "bias_d1": bias,
            "d1_target": _pick_float(d1.target, bias_at),
            "d1_range_id": _ids(d1.range_id, bias_at),
            "h4_focus_range_id": _ids(h4.range_id, focus),
            "h4_blocking_range_id": _ids(h4.range_id, blocking),
        },
        index=pd.DatetimeIndex(h4_index, name=INDEX_NAME),
    )
    return frame[list(COLUMNS)]


def empty_states(h4_index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """El resultado sin ninguna vela, con los tipos de columna de siempre."""
    index = pd.DatetimeIndex([] if h4_index is None else h4_index, name=INDEX_NAME)
    return pd.DataFrame(
        {name: pd.Series(dtype=dtype, index=index) for name, dtype in _DTYPES.items()},
        index=index,
    )


def mark_time_filter(
    ranges_h4: pd.DataFrame, key_h4_opens_ny: Iterable[str] | None = None
) -> pd.DataFrame:
    """Marca con `ignored_by_time_filter` los rangos H4 que el filtro deja fuera.

    Los rangos se detectan igual: el filtro no cambia la detección, cambia
    quién puede alinear. Sin filtro, ninguno queda marcado.
    """
    if ranges_h4.empty:
        frame = ranges_h4.copy()
        frame[IGNORED] = pd.Series(dtype=bool, index=frame.index)
        return frame
    frame = ranges_h4.copy()
    if key_h4_opens_ny is None:
        frame[IGNORED] = False
        return frame
    if H4_OPEN_NY not in frame.columns:
        raise DomainError(
            f"El filtro horario exige la columna {H4_OPEN_NY} en los rangos H4: "
            "es la hora de apertura de la vela que confirmó cada rango"
        )
    frame[IGNORED] = ~key_open_mask(frame[H4_OPEN_NY], key_h4_opens_ny)
    return frame


# --- Resumen --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StateSummary:
    """Cuánto tiempo pasó el sistema en cada estado, en velas H4."""

    total: int
    counts: dict[str, int]

    def share(self, state: str) -> float:
        """Porcentaje del tiempo en ese estado. Sin velas, cero."""
        return 100.0 * self.counts.get(state, 0) / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class AlignmentDelay:
    """Lo que tardó UN rango diario en encontrar una vela H4 a favor.

    `bars` son las velas H4 que pasaron desde que el sistema quedó en
    TARGET_DEFINIDO con ese rango de sesgo hasta el primer H4_ALINEADO. `None`
    significa que nunca llegó: con ese rango de sesgo no hubo ni una vela
    alineada, y el filtro se quedó mirando.
    """

    range_id: int
    bars: int | None


@dataclass(frozen=True, slots=True)
class AlignmentDelays:
    """Los retrasos de todos los rangos diarios completados, juntos.

    Es la pregunta de si el filtro llega tarde: un rango que se completó
    mientras el sistema seguía esperando una vela H4 a favor es una oportunidad
    que el filtro dejó pasar entera.
    """

    delays: tuple[AlignmentDelay, ...]

    @property
    def total(self) -> int:
        return len(self.delays)

    @property
    def aligned(self) -> tuple[int, ...]:
        return tuple(item.bars for item in self.delays if item.bars is not None)

    @property
    def never(self) -> int:
        return sum(1 for item in self.delays if item.bars is None)

    @property
    def mean_bars(self) -> float | None:
        values = self.aligned
        return float(np.mean(values)) if values else None

    @property
    def median_bars(self) -> float | None:
        values = self.aligned
        return float(np.median(values)) if values else None


def summarize_states(states: pd.DataFrame) -> StateSummary:
    """Cuántas velas H4 pasó el sistema en cada estado."""
    if states.empty:
        return StateSummary(total=0, counts=dict.fromkeys(STATES, 0))
    counted = states["state"].value_counts()
    return StateSummary(
        total=len(states),
        counts={name: int(counted.get(name, 0)) for name in STATES},
    )


def alignment_delays(states: pd.DataFrame, ranges_d1: pd.DataFrame) -> AlignmentDelays:
    """Velas H4 entre TARGET_DEFINIDO y el primer H4_ALINEADO, rango a rango.

    Sólo se miran los rangos diarios COMPLETADOS: son los que sí llegaron a su
    objetivo, y por tanto los que había que haber operado. Se cuenta desde la
    primera vela en que ese rango fue el sesgo con el sistema esperando, hasta
    la primera en que quedó alineado.
    """
    if states.empty or ranges_d1.empty:
        return AlignmentDelays(delays=())

    completed = set(
        ranges_d1.loc[ranges_d1["status"] == COMPLETED, "range_id"].to_numpy(dtype=int).tolist()
    )
    if not completed:
        return AlignmentDelays(delays=())

    # Sin sesgo no hay número de rango: se sustituye por un -1 que no puede
    # coincidir con ninguno, en vez de arrastrar nulos por la comparación.
    identifiers = states["d1_range_id"].fillna(-1).to_numpy(dtype=np.int64)
    state = states["state"].to_numpy(dtype=object)
    delays: list[AlignmentDelay] = []
    for identifier in sorted(completed):
        rows = np.flatnonzero(identifiers == identifier)
        if not len(rows):
            continue
        waiting = np.flatnonzero(state[rows] == TARGET_DEFINIDO)
        if not len(waiting):
            # Nunca esperó: la primera vela con ese sesgo ya traía un rango H4.
            continue
        start = rows[waiting[0]]
        after = rows[rows > start]
        hit = np.flatnonzero(state[after] == H4_ALINEADO)
        delays.append(
            AlignmentDelay(
                range_id=int(identifier),
                bars=None if not len(hit) else int(after[hit[0]] - start),
            )
        )
    return AlignmentDelays(delays=tuple(delays))


# --- Quién manda en cada instante -------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RangeArrays:
    """Los rangos como columnas de numpy, que es como se cruzan sin bucles."""

    range_id: np.ndarray
    direction: np.ndarray
    bullish: np.ndarray
    target: np.ndarray
    known: np.ndarray
    resolved: np.ndarray
    ignored: np.ndarray
    #: Posiciones ordenadas por antigüedad: primero el más viejo. Es el orden en
    #: el que se decide quién manda, y desempata el número de rango.
    order: np.ndarray

    @classmethod
    def of(cls, frame: pd.DataFrame) -> _RangeArrays:
        if frame.empty:
            empty_int = np.zeros(0, dtype=np.int64)
            return cls(
                range_id=empty_int,
                direction=np.zeros(0, dtype=object),
                bullish=np.zeros(0, dtype=bool),
                target=np.zeros(0, dtype=float),
                known=empty_int,
                resolved=empty_int,
                ignored=np.zeros(0, dtype=bool),
                order=empty_int,
            )
        missing = [name for name in (KNOWN_AT, RESOLVED_AT) if name not in frame.columns]
        if missing:
            raise DomainError(
                f"A los rangos les falta {', '.join(missing)}: la alineación se resuelve "
                "con el CIERRE de la vela que confirmó y resolvió cada rango, no con su "
                "etiqueta, que es anterior"
            )
        known = _nanos(frame[KNOWN_AT])
        if (known == _NEVER).any():
            raise DomainError(f"Hay rangos sin {KNOWN_AT}: no se sabe cuándo se confirmaron")
        range_id = frame["range_id"].to_numpy(dtype=np.int64)
        ignored = (
            frame[IGNORED].to_numpy(dtype=bool)
            if IGNORED in frame.columns
            else np.zeros(len(frame), dtype=bool)
        )
        return cls(
            range_id=range_id,
            direction=frame["direction"].to_numpy(dtype=object),
            bullish=(frame["direction"] == BULLISH).to_numpy(dtype=bool),
            target=frame["target"].to_numpy(dtype=float),
            known=known,
            resolved=_nanos(frame[RESOLVED_AT]),
            ignored=ignored,
            order=np.lexsort((range_id, known)),
        )


def _latest_alive_within(
    arrays: _RangeArrays, positions: np.ndarray, clock: np.ndarray
) -> np.ndarray:
    """Como `_latest_alive`, pero mirando sólo a los rangos de `positions`."""
    if not len(positions):
        return np.full(len(clock), -1, dtype=np.int64)
    keep = np.isin(arrays.order, positions)
    return _latest_alive(arrays.known, arrays.resolved, arrays.order[keep], clock)


def _latest_alive(
    known: np.ndarray, resolved: np.ndarray, order: np.ndarray, clock: np.ndarray
) -> np.ndarray:
    """Posición del rango vivo más reciente en cada instante, o -1 si no hay.

    Se pinta de atrás hacia delante: el rango más nuevo reclama primero el tramo
    en el que está vivo, y los anteriores rellenan sólo lo que queda libre. Así
    sale gratis la parte que una búsqueda del último confirmado no da: cuando el
    rango que mandaba se resuelve, vuelve a mandar el de antes si sigue vivo.

    Un rango se conoce desde que CIERRA su vela de confirmación y sigue vivo
    mientras el reloj no llega al cierre de la vela que lo resolvió.
    """
    winner = np.full(len(clock), -1, dtype=np.int64)
    if not len(order) or not len(clock):
        return winner
    for position in order[::-1]:
        start = int(np.searchsorted(clock, known[position], side="left"))
        end = int(np.searchsorted(clock, resolved[position], side="left"))
        if end <= start:
            continue
        tramo = winner[start:end]
        np.copyto(tramo, position, where=tramo < 0)
    return winner


def _clock(h4_index: pd.DatetimeIndex) -> np.ndarray:
    """Los cierres de vela H4 en enteros, comprobando que van en orden."""
    index = pd.DatetimeIndex(h4_index)
    if len(index) and index.tz is None:
        raise DomainError("Los cierres de vela H4 exigen un índice tz-aware en UTC")
    if not index.is_monotonic_increasing:
        raise DomainError("Los cierres de vela H4 tienen que ir en orden")
    if not len(index):
        return np.zeros(0, dtype=np.int64)
    return _int64(index.tz_convert("UTC"))


def _nanos(column: pd.Series) -> np.ndarray:
    """Instantes en enteros, con «nunca» donde no hay marca."""
    values = pd.DatetimeIndex(column)
    values = values.tz_convert("UTC") if values.tz is not None else values.tz_localize("UTC")
    return np.where(values.isna(), _NEVER, _int64(values))


def _int64(index: pd.DatetimeIndex) -> np.ndarray:
    """Los instantes como enteros de nanosegundos desde la época."""
    return np.asarray(index.to_numpy(dtype="datetime64[ns]"), dtype=np.int64)


def _pick(values: np.ndarray, positions: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """El valor de cada posición, y `None` donde la posición no existe."""
    if not len(values):
        return np.full(len(positions), None, dtype=object)
    return np.asarray(np.where(valid, values[np.maximum(positions, 0)], None), dtype=object)


def _pick_float(values: np.ndarray, positions: np.ndarray) -> np.ndarray:
    if not len(values):
        return np.full(len(positions), np.nan, dtype=float)
    return np.asarray(
        np.where(positions >= 0, values[np.maximum(positions, 0)], np.nan), dtype=float
    )


def _ids(range_id: np.ndarray, positions: np.ndarray) -> pd.api.extensions.ExtensionArray:
    """Números de rango como entero anulable: sin rango no hay número, no un cero."""
    if not len(range_id):
        return pd.array([pd.NA] * len(positions), dtype="Int64")
    values = pd.array(range_id[np.maximum(positions, 0)], dtype="Int64")
    values[positions < 0] = pd.NA
    return cast("pd.api.extensions.ExtensionArray", values)


def _mode(alignment_mode: str) -> str:
    if alignment_mode not in MODES:
        raise DomainError(
            f"alignment_mode desconocido: {alignment_mode!r}. Disponibles: {', '.join(MODES)}"
        )
    return alignment_mode


def describe_mode(alignment_mode: str) -> str:
    """Cómo se cuenta el modo en pantalla."""
    mode = _mode(alignment_mode)
    if mode == LATEST:
        return "latest: manda el rango H4 vivo más reciente"
    return "strict: además, ningún rango H4 vivo puede ir en contra"
