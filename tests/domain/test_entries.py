"""Las entradas en 15M tras la confirmación de H4.

Las velas se escriben en H1, como en ``test_confirmation.py``, y cada una se
parte en cuatro velas de 15M iguales; lo que se quiera mover dentro de una hora
se escribe ya en 15M. H1, H3 y H4 salen de agrupar las de 15M.

El caso base es el de ``test_h4_confirmation.py``: la caja de las 02:00 va de
103 a 105, la vela de H1 de las 02:00 NY (105,3 / 104,2 / 104,4) da la señal
bajista y H4 la confirma a su cierre, a las 03:00. Se entra al cierre de la
vela de 15M de las 03:00; el stop es 105,3 y el take, el mínimo de la caja, 103.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chronos.domain.crt.entries import ENTRY_RULES, STOP, TAKE, TIME, EntryRules, entries
from chronos.domain.crt.ranges import BEARISH
from tests.domain.test_confirmation import SUBIDA

Candle = tuple[float, float, float]


def _quarters(*hours: Candle) -> list[Candle]:
    return [candle for candle in hours for _ in range(4)]


def _group(frame: pd.DataFrame, size: int) -> pd.DataFrame:
    """Las velas CERRADAS de la temporalidad que agrupa ``size`` de ``frame``."""
    count = len(frame) // size * size
    grouped = frame.iloc[:count]
    return pd.DataFrame(
        {
            "open": grouped["open"].to_numpy()[0::size],
            "high": grouped["high"].to_numpy().reshape(-1, size).max(axis=1),
            "low": grouped["low"].to_numpy().reshape(-1, size).min(axis=1),
            "close": grouped["close"].to_numpy()[size - 1 :: size],
            "volume": 0.0,
        },
        index=grouped.index[0::size],
    )


def _m15(*candles: Candle, start: str = "2024-01-08 17:00") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(candles), freq="15min", tz="America/New_York")
    high, low, close = (np.array(values, dtype=float) for values in zip(*candles, strict=True))
    return pd.DataFrame(
        {"open": np.r_[close[:1], close[:-1]], "high": high, "low": low, "close": close,
         "volume": 0.0},
        index=index.tz_convert("UTC"),
    )


def _entries(m15: pd.DataFrame, rules: EntryRules = ENTRY_RULES) -> pd.DataFrame:
    h1 = _group(m15, 4)
    return entries(h1, _group(h1, 3), _group(h1, 4), m15, rules=rules)


#: Hasta la señal de las 02:00, incluida: 40 velas de 15M. La de las 03:00, la
#: de la entrada, es la 40.
HASTA_LA_SENAL = _quarters(*SUBIDA, (105.3, 104.2, 104.4))
ENTRADA = (104.5, 104.0, 104.2)
QUIETA = (104.5, 104.0, 104.2)


def test_entra_al_cierre_de_la_vela_de_15m_y_llega_al_take() -> None:
    operaciones = _entries(_m15(*HASTA_LA_SENAL, ENTRADA, QUIETA, (104.3, 102.9, 103.1)))
    assert len(operaciones) == 1
    fila = operaciones.iloc[0]
    assert fila["direction"] == BEARISH
    assert fila["bar"] == 40
    assert (fila["entry"], fila["stop"], fila["take"]) == (104.2, 105.3, 103.0)
    assert (fila["exit_bar"], fila["exit"], fila["exit_reason"]) == (42, 103.0, TAKE)
    assert fila["r"] == pytest.approx((104.2 - 103.0) / (105.3 - 104.2))


def test_toca_el_stop() -> None:
    fila = _entries(_m15(*HASTA_LA_SENAL, ENTRADA, (105.4, 104.1, 105.0))).iloc[0]
    assert (fila["exit_bar"], fila["exit"], fila["exit_reason"], fila["r"]) == (
        41, 105.3, STOP, -1.0
    )


def test_si_una_vela_toca_el_stop_y_el_take_cuenta_el_stop() -> None:
    fila = _entries(_m15(*HASTA_LA_SENAL, ENTRADA, (105.4, 102.9, 104.0))).iloc[0]
    assert (fila["exit_reason"], fila["r"]) == (STOP, -1.0)


def test_el_stop_es_el_extremo_desde_la_senal_hasta_la_entrada() -> None:
    # La vela de 15M de la entrada sube más que la señal: el stop es su máximo.
    fila = _entries(_m15(*HASTA_LA_SENAL, (105.5, 104.0, 104.2))).iloc[0]
    assert fila["stop"] == 105.5


def test_sin_tocar_nada_se_cierra_a_las_16_30() -> None:
    # De las 03:00 a las 16:30 NY hay 54 velas de 15M: la de la entrada y 53 más.
    velas = (*HASTA_LA_SENAL, ENTRADA, *[QUIETA] * 52, (104.5, 104.0, 104.1), QUIETA)
    fila = _entries(_m15(*velas)).iloc[0]
    assert (fila["exit_bar"], fila["exit"], fila["exit_reason"]) == (93, 104.1, TIME)
    assert fila["r"] == pytest.approx((104.2 - 104.1) / (105.3 - 104.2))


def test_sin_serie_hasta_la_salida_sigue_abierta() -> None:
    fila = _entries(_m15(*HASTA_LA_SENAL, ENTRADA, QUIETA)).iloc[0]
    assert (fila["exit_bar"], fila["exit_reason"]) == (-1, "")
    assert np.isnan(fila["exit"]) and np.isnan(fila["r"])


def test_si_la_entrada_ya_ha_pasado_el_take_no_hay_operacion() -> None:
    assert _entries(_m15(*HASTA_LA_SENAL, (104.5, 102.5, 102.8))).empty


def test_sin_la_vela_de_15m_de_la_entrada_no_hay_operacion() -> None:
    assert _entries(_m15(*HASTA_LA_SENAL)).empty


def test_sin_confirmacion_no_hay_operacion() -> None:
    assert _entries(_m15(*_quarters(*SUBIDA), QUIETA)).empty


def _aleatoria(dias: int, semilla: int) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    size = 96 * dias
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size))
    high = close + rng.uniform(0.02, 0.45, size)
    low = close - rng.uniform(0.02, 0.45, size)
    return _m15(*zip(high, low, close, strict=True))


def test_como_mucho_dos_al_dia_y_las_primeras() -> None:
    m15 = _aleatoria(40, 5)
    todas = _entries(m15)
    assert len(todas) >= 4, "la serie debería dar varias operaciones"
    dias = pd.DatetimeIndex(m15.index[todas["bar"].to_numpy()]).tz_convert(
        "America/New_York"
    ).date
    assert pd.Series(dias).value_counts().max() <= 2
    una = _entries(m15, EntryRules(max_per_day=1))
    primeras = todas.loc[~pd.Series(dias).duplicated().to_numpy()].reset_index(drop=True)
    pd.testing.assert_frame_equal(una, primeras)


def test_una_operacion_no_cambia_con_velas_posteriores() -> None:
    """Con la serie cortada en cada vela de 15M, las entradas sabidas hasta ahí
    son las mismas, y cada salida se sabe al cierre de su vela y no antes."""
    m15 = _aleatoria(20, 11)
    completas = _entries(m15)
    assert len(completas) >= 2, "la serie debería dar varias operaciones"
    for posicion in range(0, len(m15), 3):
        parciales = _entries(m15.iloc[: posicion + 1])
        esperadas = completas[completas["bar"] <= posicion].reset_index(drop=True)
        cerradas = esperadas["exit_bar"] <= posicion
        pd.testing.assert_frame_equal(
            parciales[cerradas.to_numpy()], esperadas[cerradas], obj=f"vela {posicion}"
        )
        assert (parciales.loc[~cerradas.to_numpy(), "exit_bar"] == -1).all(), f"vela {posicion}"
