"""La caja de las 02:00 NY en H3: de dónde sale y cuándo la sustituye la vela
de las 02:00.

Las velas se escriben a mano (máximo, mínimo, cierre) sobre la rejilla de H3 de
Nueva York: 17:00, 20:00, 23:00 y 02:00, así que la vela de las 02:00 es la
cuarta y la anterior, la de las 23:00, la tercera.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from chronos.domain.crt.decision_box import (
    BREAKOUT,
    PREVIOUS_BAR,
    RANGE,
    decision_boxes,
)
from chronos.domain.crt.ranges import BEARISH


def _h3(*candles: tuple[float, float, float], start: str = "2024-01-08 17:00") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(candles), freq="3h", tz="America/New_York")
    high, low, close = zip(*candles, strict=True) if candles else ((), (), ())
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 0.0},
        index=index.tz_convert("UTC"),
    )


#: Sin rango: cada vela saca el máximo y cierra fuera (expansión).
SUBIDA = ((101.0, 99.0, 100.5), (102.0, 100.0, 101.8), (103.0, 101.0, 102.9))
#: Rango bajista vivo a las 02:00: la vela de las 20:00 le saca el máximo a la
#: de las 17:00 y cierra dentro; la de las 23:00 no lo frena.
RANGO_BAJISTA = ((110.0, 100.0, 105.0), (112.0, 104.0, 106.0), (109.0, 105.0, 107.0))


def test_sin_rango_vivo_la_caja_es_la_vela_anterior() -> None:
    cajas = decision_boxes(_h3(*SUBIDA, (102.8, 101.5, 102.0)))
    assert len(cajas) == 1
    caja = cajas.iloc[0]
    assert caja["kind"] == PREVIOUS_BAR
    assert caja["direction"] == 0
    assert (caja["reference"], caja["known"], caja["decision"]) == (2, 2, 3)
    assert (caja["high"], caja["low"]) == (103.0, 101.0)
    assert not caja["replaced"]


def test_con_rango_vivo_la_caja_es_su_vela_1() -> None:
    cajas = decision_boxes(_h3(*RANGO_BAJISTA, (108.0, 104.0, 106.0)))
    assert len(cajas) == 1
    caja = cajas.iloc[0]
    assert caja["kind"] == RANGE
    assert caja["direction"] == BEARISH
    assert (caja["reference"], caja["known"]) == (0, 2)
    assert (caja["high"], caja["low"]) == (110.0, 100.0)


def test_un_rango_que_muere_en_la_vela_anterior_no_cuenta() -> None:
    # La vela de las 23:00 toca el mínimo de la vela 1: el rango termina ahí.
    velas = (*RANGO_BAJISTA[:2], (109.0, 99.0, 101.0), (108.0, 100.0, 104.0))
    caja = decision_boxes(_h3(*velas)).iloc[0]
    assert caja["kind"] == PREVIOUS_BAR
    assert (caja["high"], caja["low"]) == (109.0, 99.0)


def test_si_la_vela_de_las_2_cierra_fuera_pasa_a_ser_la_caja() -> None:
    cajas = decision_boxes(_h3(*RANGO_BAJISTA, (112.0, 106.0, 111.0)))
    assert cajas["kind"].tolist() == [RANGE, BREAKOUT]
    assert cajas["replaced"].tolist() == [True, False]
    ruptura = cajas.iloc[1]
    assert (ruptura["reference"], ruptura["known"], ruptura["decision"]) == (3, 3, 3)
    assert (ruptura["high"], ruptura["low"]) == (112.0, 106.0)


def test_cerrar_fuera_por_abajo_tambien_la_sustituye() -> None:
    cajas = decision_boxes(_h3(*SUBIDA, (102.0, 100.0, 100.5)))
    assert cajas["kind"].tolist() == [PREVIOUS_BAR, BREAKOUT]
    assert (cajas.iloc[1]["high"], cajas.iloc[1]["low"]) == (102.0, 100.0)


def test_sacar_la_mecha_y_cerrar_dentro_es_rechazo_y_la_caja_se_mantiene() -> None:
    cajas = decision_boxes(_h3(*RANGO_BAJISTA, (113.0, 106.0, 108.0)))
    assert cajas["kind"].tolist() == [RANGE]
    assert not cajas.iloc[0]["replaced"]


def test_cerrar_justo_en_el_extremo_no_es_cerrar_fuera() -> None:
    cajas = decision_boxes(_h3(*SUBIDA, (103.5, 101.5, 103.0)))
    assert cajas["kind"].tolist() == [PREVIOUS_BAR]


def test_vale_hasta_las_12_de_nueva_york_con_su_horario_de_verano() -> None:
    invierno = decision_boxes(_h3(*SUBIDA, (102.8, 101.5, 102.0))).iloc[0]
    verano = decision_boxes(_h3(*SUBIDA, (102.8, 101.5, 102.0), start="2024-07-08 17:00")).iloc[0]
    assert invierno["until"] == pd.Timestamp("2024-01-09 17:00", tz="UTC")
    assert verano["until"] == pd.Timestamp("2024-07-09 16:00", tz="UTC")
    # En verano la vela de las 02:00 NY es la de las 06:00 UTC, y se encuentra igual.
    assert verano["decision"] == 3


def test_sin_vela_de_las_2_no_hay_caja() -> None:
    assert decision_boxes(_h3()).empty
    assert decision_boxes(_h3((101.0, 99.0, 100.0))).empty
    assert decision_boxes(_h3(*SUBIDA)).empty
    # La vela de las 02:00 como primera de la serie: no tiene vela anterior.
    assert decision_boxes(_h3((101.0, 99.0, 100.0), start="2024-01-09 02:00")).empty


def test_la_caja_de_un_dia_no_cambia_con_velas_posteriores() -> None:
    """Sin mirar al futuro: cortar la serie en la vela de las 02:00 de un día da
    las mismas cajas de ese día que la serie entera."""
    rng = np.random.default_rng(3)
    size = 8 * 10
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, size))
    high = close + rng.uniform(0.1, 1.5, size)
    low = close - rng.uniform(0.1, 1.5, size)
    velas = _h3(*zip(high, low, close, strict=True))
    completas = decision_boxes(velas)
    assert completas["kind"].nunique() == 3, "la serie debería dar los tres tipos de caja"
    for decision in completas["decision"].unique():
        cortadas = decision_boxes(velas.iloc[: decision + 1])
        assert cortadas[cortadas["decision"] == decision].reset_index(drop=True).equals(
            completas[completas["decision"] == decision].reset_index(drop=True)
        )
