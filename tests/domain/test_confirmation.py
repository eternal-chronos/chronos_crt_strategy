"""La señal de confirmación en H1 sobre la caja de las 02:00 de H3.

Las velas de H1 se escriben a mano (máximo, mínimo, cierre) desde las 17:00 de
Nueva York y H3 sale de agruparlas de tres en tres, como en la plataforma:
H1 0-2 → H3 de las 17:00, ..., H1 9-11 → H3 de las 02:00, H1 12-14 → la de las
05:00. Las nueve primeras suben sin formar rango en H3, así que la caja de las
02:00 es la vela de H3 de las 23:00: máximo 105, mínimo 103.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from chronos.domain.crt.confirmation import confirmation_signals
from chronos.domain.crt.decision_box import decision_boxes
from chronos.domain.crt.ranges import BEARISH, BULLISH


def _h1(*candles: tuple[float, float, float], start: str = "2024-01-08 17:00") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(candles), freq="1h", tz="America/New_York")
    high, low, close = (np.array(values, dtype=float) for values in zip(*candles, strict=True))
    return pd.DataFrame(
        {"open": np.r_[close[:1], close[:-1]], "high": high, "low": low, "close": close,
         "volume": 0.0},
        index=index.tz_convert("UTC"),
    )


def _h3(h1: pd.DataFrame) -> pd.DataFrame:
    """Las velas de H3 CERRADAS: los grupos completos de tres de H1."""
    size = len(h1) // 3 * 3
    grouped = h1.iloc[:size]
    return pd.DataFrame(
        {
            "open": grouped["open"].to_numpy()[0::3],
            "high": grouped["high"].to_numpy().reshape(-1, 3).max(axis=1),
            "low": grouped["low"].to_numpy().reshape(-1, 3).min(axis=1),
            "close": grouped["close"].to_numpy()[2::3],
            "volume": 0.0,
        },
        index=grouped.index[0::3],
    )


def _signals(*candles: tuple[float, float, float]) -> pd.DataFrame:
    h1 = _h1(*candles)
    return confirmation_signals(h1, _h3(h1))


#: 17:00 a 01:00 NY: suben medio punto por hora sin formar rango en H3.
SUBIDA = tuple((base + 1.0, base, base + 0.9) for base in np.arange(100.0, 104.5, 0.5))
#: Dentro de la caja sin tocar ninguno de sus extremos ni barrer a la anterior.
QUIETA = (104.5, 103.5, 104.0)


def test_la_caja_de_partida_es_la_vela_de_las_23() -> None:
    h1 = _h1(*SUBIDA, QUIETA)
    caja = decision_boxes(_h3(h1)).iloc[0]
    assert (caja["kind"], caja["high"], caja["low"]) == ("vela_previa", 105.0, 103.0)


def test_toca_el_minimo_y_la_siguiente_hace_turtle_soup_alcista() -> None:
    # 03:00 toca el mínimo de la caja y cierra por debajo de la anterior (no es
    # turtle soup); 04:00 le saca el mínimo a la de 03:00 y cierra por encima,
    # fuera de la caja: da igual.
    senales = _signals(*SUBIDA, QUIETA, (104.2, 102.8, 103.0), (103.4, 102.5, 103.2))
    assert len(senales) == 1
    senal = senales.iloc[0]
    assert senal["direction"] == BULLISH
    assert senal["bar"] == 11
    assert (senal["swept"], senal["opposite"]) == (102.8, 104.2)
    assert senal["h3_open"] == pd.Timestamp("2024-01-09 07:00", tz="UTC")
    assert senal["h3_close"] == pd.Timestamp("2024-01-09 10:00", tz="UTC")


def test_la_misma_vela_puede_tocar_el_maximo_y_hacer_el_turtle_soup() -> None:
    senales = _signals(*SUBIDA, (105.3, 104.2, 104.6))
    assert len(senales) == 1
    senal = senales.iloc[0]
    assert senal["direction"] == BEARISH
    assert senal["bar"] == 9
    assert (senal["swept"], senal["opposite"]) == (105.0, 104.0)


def test_un_turtle_soup_sin_haber_tocado_la_caja_no_es_senal() -> None:
    assert _signals(*SUBIDA, QUIETA, (104.2, 103.2, 103.6), (104.0, 103.1, 103.7)).empty


def test_el_turtle_soup_de_una_vela_de_dos_atras_no_cuenta() -> None:
    # 02:00 toca la caja; 03:00 baja más y cierra por debajo; 04:00 le saca el
    # mínimo a la de 02:00, no a la de 03:00: no es turtle soup.
    velas = (*SUBIDA, (104.0, 102.8, 103.5), (103.5, 102.7, 102.72), (103.4, 102.75, 103.3))
    assert _signals(*velas).empty


def test_la_vela_que_barre_a_la_anterior_por_los_dos_lados_no_da_senal() -> None:
    assert _signals(*SUBIDA, QUIETA, (104.6, 102.8, 104.0)).empty


def test_cada_vela_de_h3_empieza_de_cero() -> None:
    # La de las 02:00 de H3 toca el mínimo y cierra dentro de la caja; en la de
    # las 05:00 hay turtle soup, pero esa vela de H3 no ha tocado la caja.
    velas = (*SUBIDA, QUIETA, (104.2, 102.8, 103.0), (103.5, 103.2, 103.4),
             (103.8, 103.3, 103.6), (103.7, 103.25, 103.5))
    assert _signals(*velas).empty


def test_solo_cuenta_la_primera_senal_de_cada_vela_de_h3() -> None:
    velas = (*SUBIDA, QUIETA, (104.2, 102.8, 103.0), (103.4, 102.5, 103.2))
    doble = (*SUBIDA, (104.2, 102.8, 103.9), (104.0, 102.7, 103.5), (103.9, 102.6, 103.4))
    assert _signals(*velas)["bar"].tolist() == [11]
    assert _signals(*doble)["bar"].tolist() == [10]


def test_la_caja_barrida_muere_en_la_vela_de_h1_que_toca_su_segundo_extremo() -> None:
    # La de las 05:00 de H3 sube por encima de la caja y a las 07:00 hace
    # turtle soup bajista a la de las 06:00. Si la de las 02:00 ya tocó el
    # mínimo, la de las 05:00 (H1) toca el segundo extremo y mata la caja: el
    # turtle soup llega después y no cuenta.
    subida_de_las_05 = ((103.8, 103.2, 103.4), (103.6, 103.3, 103.5), (105.5, 103.4, 105.3),
                        (105.8, 105.2, 105.6), (106.0, 105.4, 105.5))
    viva = _signals(*SUBIDA, QUIETA, *subida_de_las_05)
    barrida = _signals(*SUBIDA, (104.0, 102.9, 103.5), *subida_de_las_05)
    assert viva["bar"].tolist() == [14]
    assert barrida.empty


def test_la_vela_de_h1_que_cierra_despues_de_las_12_no_cuenta() -> None:
    # De 02:00 a 10:00 NY, quietas; la de las 11:00 cierra a las 12:00 y cuenta,
    # la de las 12:00 cierra a las 13:00 y no.
    quietas = (QUIETA,) * 9
    soup = (104.0, 102.5, 103.8)
    a_las_11 = _signals(*SUBIDA, *quietas, soup)
    a_las_12 = _signals(*SUBIDA, *quietas, QUIETA, soup)
    assert a_las_11["bar"].tolist() == [18]
    assert a_las_12.empty


def test_sin_caja_o_sin_velas_no_hay_senales() -> None:
    assert _signals(*SUBIDA[:1]).empty
    assert _signals(*SUBIDA[:5]).empty
    vacio = _h1(*SUBIDA).iloc[:0]
    assert confirmation_signals(vacio, vacio).empty


def test_una_senal_no_cambia_con_velas_posteriores() -> None:
    """Sin mirar al futuro: con H1 cortado en cada vela y sólo las velas de H3
    que ya han cerrado, las señales hasta ahí son las mismas que con todo."""
    rng = np.random.default_rng(7)
    size = 24 * 12
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.6, size))
    high = close + rng.uniform(0.05, 0.9, size)
    low = close - rng.uniform(0.05, 0.9, size)
    h1 = _h1(*zip(high, low, close, strict=True))
    completas = confirmation_signals(h1, _h3(h1))
    assert len(completas) >= 3, "la serie debería dar varias señales"
    for posicion in range(size):
        cortado = h1.iloc[: posicion + 1]
        h3_cerradas = _h3(h1).iloc[: (posicion + 1) // 3]
        parciales = confirmation_signals(cortado, h3_cerradas)
        esperadas = completas[completas["bar"] <= posicion].reset_index(drop=True)
        pd.testing.assert_frame_equal(parciales, esperadas, obj=f"vela {posicion}")
