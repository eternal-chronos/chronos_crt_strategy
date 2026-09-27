"""La confirmación en H4 de la señal de H1.

Las mismas velas de H1 a mano que en ``test_confirmation.py``, desde las 17:00
de Nueva York. H4 sale de agruparlas de cuatro en cuatro: H1 4-7 → H4 de las
21:00 (máximo 104,5, mínimo 102) y H1 8-11 → H4 de la 01:00, la que está en
curso cuando llega la señal de H1 de las 02:00.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from chronos.domain.crt.confirmation import confirmation_signals
from chronos.domain.crt.h4_confirmation import h4_confirmations
from chronos.domain.crt.ranges import BEARISH
from tests.domain.test_confirmation import SUBIDA, _h1, _h3


def _h4(h1: pd.DataFrame) -> pd.DataFrame:
    """Las velas de H4 CERRADAS: los grupos completos de cuatro de H1."""
    size = len(h1) // 4 * 4
    grouped = h1.iloc[:size]
    return pd.DataFrame(
        {
            "open": grouped["open"].to_numpy()[0::4],
            "high": grouped["high"].to_numpy().reshape(-1, 4).max(axis=1),
            "low": grouped["low"].to_numpy().reshape(-1, 4).min(axis=1),
            "close": grouped["close"].to_numpy()[3::4],
            "volume": 0.0,
        },
        index=grouped.index[0::4],
    )


def _confirmations(*candles: tuple[float, float, float]) -> pd.DataFrame:
    h1 = _h1(*candles)
    return h4_confirmations(h1, _h3(h1), _h4(h1))


#: 02:00: toca el máximo de la caja (105) y le hace turtle soup bajista a la de
#: la 01:00; cierra por encima del máximo de la H4 anterior (104,5).
SENAL = (105.3, 104.2, 104.6)


def test_la_senal_de_h1_y_el_turtle_soup_de_h4_en_la_misma_vela() -> None:
    confirmaciones = _confirmations(*SUBIDA, (105.3, 104.2, 104.4))
    assert len(confirmaciones) == 1
    fila = confirmaciones.iloc[0]
    assert fila["direction"] == BEARISH
    assert (fila["signal"], fila["bar"], fila["previous"]) == (0, 9, 1)
    assert (fila["swept"], fila["opposite"]) == (104.5, 102.0)
    assert fila["h4_open"] == pd.Timestamp("2024-01-09 06:00", tz="UTC")
    assert fila["h4_close"] == pd.Timestamp("2024-01-09 10:00", tz="UTC")


def test_h4_puede_confirmar_en_un_cierre_de_h1_posterior() -> None:
    confirmaciones = _confirmations(*SUBIDA, SENAL, (104.9, 104.3, 104.4))
    assert confirmaciones["bar"].tolist() == [10]


def test_sin_turtle_soup_en_h4_no_hay_confirmacion() -> None:
    velas = (*SUBIDA, SENAL, (104.9, 104.6, 104.7), (104.8, 104.55, 104.7))
    assert len(confirmation_signals(_h1(*velas), _h3(_h1(*velas)))) == 1
    assert _confirmations(*velas).empty


def test_la_h4_que_barre_los_dos_lados_de_la_anterior_no_confirma() -> None:
    # 03:00 baja por debajo del mínimo de la H4 anterior (102) y cierra por
    # debajo de 104,5: la H4 en curso ya le ha sacado los dos extremos.
    assert _confirmations(*SUBIDA, SENAL, (104.9, 101.9, 104.4)).empty


def test_despues_de_cerrar_la_vela_de_h3_ya_no_confirma() -> None:
    # A las 05:00 cierra la H3 de la señal y abre otra H4, que sí le hace
    # turtle soup bajista a la anterior: ya no es la señal de antes.
    velas = (*SUBIDA, SENAL, (104.9, 104.6, 104.7), (104.9, 104.6, 104.7), (105.5, 104.8, 105.0))
    assert _confirmations(*velas).empty


def test_sin_senal_o_sin_h4_no_hay_confirmacion() -> None:
    assert _confirmations(*SUBIDA).empty
    h1 = _h1(*SUBIDA, (105.3, 104.2, 104.4))
    assert h4_confirmations(h1, _h3(h1), _h4(h1).iloc[:1]).empty


def test_una_confirmacion_no_cambia_con_velas_posteriores() -> None:
    """Con H1 cortado en cada vela y sólo las H3 y H4 que ya han cerrado, lo
    confirmado hasta ahí es lo mismo que con todo el histórico."""
    rng = np.random.default_rng(11)
    size = 24 * 20
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.6, size))
    high = close + rng.uniform(0.05, 0.9, size)
    low = close - rng.uniform(0.05, 0.9, size)
    h1 = _h1(*zip(high, low, close, strict=True))
    completas = h4_confirmations(h1, _h3(h1), _h4(h1))
    assert len(completas) >= 2, "la serie debería dar varias confirmaciones"
    for posicion in range(size):
        cortado = h1.iloc[: posicion + 1]
        parciales = h4_confirmations(
            cortado, _h3(h1).iloc[: (posicion + 1) // 3], _h4(h1).iloc[: (posicion + 1) // 4]
        )
        esperadas = completas[completas["bar"] <= posicion].reset_index(drop=True)
        pd.testing.assert_frame_equal(parciales, esperadas, obj=f"vela {posicion}")
