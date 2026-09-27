"""La caja de las 02:00 NY en H3: de dónde sale y qué la sustituye.

Las velas se escriben a mano (máximo, mínimo, cierre) sobre la rejilla de H3 de
Nueva York: 17:00, 20:00, 23:00, 02:00, 05:00, 08:00 y 11:00, así que la vela de
las 02:00 es la cuarta (posición 3) y la anterior, la de las 23:00, la tercera.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from chronos.domain.crt.decision_box import (
    BREAKOUT,
    PREVIOUS_BAR,
    RANGE,
    SWEEP,
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
    assert (caja["reference"], caja["known"]) == (2, 2)
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
    assert (ruptura["reference"], ruptura["known"]) == (3, 3)
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
    assert verano["known"] == 2


def test_la_caja_existe_antes_de_que_abra_la_vela_de_las_2() -> None:
    """Un minuto antes: basta con que haya cerrado la vela anterior."""
    cajas = decision_boxes(_h3(*SUBIDA))
    assert cajas["kind"].tolist() == [PREVIOUS_BAR]
    assert cajas.iloc[0]["known"] == 2


def test_sin_vela_que_cierre_a_las_2_no_hay_caja() -> None:
    assert decision_boxes(_h3()).empty
    assert decision_boxes(_h3((101.0, 99.0, 100.0))).empty
    assert decision_boxes(_h3(*SUBIDA[:2])).empty


#: El caso de la captura del oro (2023-01-17). La vela de las 02:00 cierra por
#: debajo de la de las 23:00 y pasa a ser la caja; la de las 05:00 le toca los
#: dos extremos y cierra dentro: la caja pasa a ser ella.
ORO = (
    (1920.0, 1912.0, 1913.0),
    (1916.0, 1908.0, 1909.0),
    (1912.0, 1907.0, 1907.5),
    (1911.89, 1905.69, 1906.48),
    (1913.79, 1904.17, 1909.29),
    (1912.0, 1906.0, 1910.0),
)


def test_la_vela_que_toca_los_dos_extremos_pasa_a_ser_la_caja() -> None:
    cajas = decision_boxes(_h3(*ORO))
    assert cajas["kind"].tolist() == [PREVIOUS_BAR, BREAKOUT, SWEEP]
    assert cajas["replaced"].tolist() == [True, True, False]
    barrido = cajas.iloc[2]
    assert (barrido["reference"], barrido["known"]) == (4, 4)
    assert (barrido["high"], barrido["low"]) == (1913.79, 1904.17)


def test_tocar_un_extremo_y_despues_el_otro_tambien_la_inhabilita() -> None:
    # La de las 02:00 toca el máximo y cierra dentro (rechazo); la de las 05:00
    # no toca nada; la de las 08:00 toca el mínimo: es ella la nueva caja.
    velas = (*SUBIDA, (103.2, 101.5, 102.0), (102.8, 101.6, 102.2), (102.5, 100.8, 101.2))
    cajas = decision_boxes(_h3(*velas))
    assert cajas["kind"].tolist() == [PREVIOUS_BAR, SWEEP]
    assert (cajas.iloc[1]["reference"], cajas.iloc[1]["known"]) == (5, 5)
    assert (cajas.iloc[1]["high"], cajas.iloc[1]["low"]) == (102.5, 100.8)


def test_despues_de_las_2_cerrar_fuera_no_la_inhabilita() -> None:
    velas = (*SUBIDA, (102.8, 101.5, 102.0), (104.0, 102.5, 103.8))
    assert decision_boxes(_h3(*velas))["kind"].tolist() == [PREVIOUS_BAR]


def test_la_caja_nueva_puede_volver_a_quedar_inhabilitada() -> None:
    velas = (*ORO[:5], (1914.0, 1904.0, 1910.0))
    cajas = decision_boxes(_h3(*velas))
    assert cajas["kind"].tolist() == [PREVIOUS_BAR, BREAKOUT, SWEEP, SWEEP]
    assert cajas.iloc[3]["reference"] == 5


def test_la_vela_que_cierra_despues_de_las_12_no_cuenta() -> None:
    # La de las 11:00 cierra a las 14:00: aunque toque los dos extremos, la
    # caja del día ya no cambia.
    velas = (*SUBIDA, (102.8, 101.5, 102.0), (102.8, 101.5, 102.0), (102.8, 101.5, 102.0),
             (110.0, 90.0, 100.0))
    assert decision_boxes(_h3(*velas))["kind"].tolist() == [PREVIOUS_BAR]


def _actual(cajas: pd.DataFrame, posicion: int) -> tuple | None:
    sabidas = cajas[cajas["known"] <= posicion]
    if sabidas.empty:
        return None
    ultima = sabidas.iloc[-1]
    return (ultima["kind"], ultima["reference"], ultima["high"], ultima["low"], ultima["until"])


def test_la_caja_actual_no_cambia_con_velas_posteriores() -> None:
    """Sin mirar al futuro: la caja vigente al cierre de cada vela es la misma
    con la serie cortada en esa vela que con la serie entera."""
    rng = np.random.default_rng(3)
    size = 8 * 10
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, size))
    high = close + rng.uniform(0.1, 1.5, size)
    low = close - rng.uniform(0.1, 1.5, size)
    velas = _h3(*zip(high, low, close, strict=True))
    completas = decision_boxes(velas)
    assert set(completas["kind"]) == {RANGE, PREVIOUS_BAR, BREAKOUT, SWEEP}, (
        "la serie debería dar los cuatro tipos de caja"
    )
    for posicion in range(size):
        assert _actual(decision_boxes(velas.iloc[: posicion + 1]), posicion) == _actual(
            completas, posicion
        ), f"vela {posicion}"
