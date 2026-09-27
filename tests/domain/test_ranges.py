"""Rangos CRT: cuándo nacen y qué los frena.

Las velas se escriben a mano (máximo, mínimo, cierre): cada test es un dibujo
que se puede comprobar con el dedo. El primer caso es el de las capturas del
propietario en el oro: vela 1 alcista de 1892 a 1922, vela 2 que saca el máximo
hasta 1929 y cierra en 1913,5 dentro.
"""

from __future__ import annotations

import pandas as pd

from chronos.domain.crt.ranges import (
    BEARISH,
    BULLISH,
    CLOSED_BEYOND,
    REJECTED,
    TARGET_HIT,
    crt_ranges,
)


def _bars(*candles: tuple[float, float, float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(candles), freq="D", tz="UTC")
    high, low, close = zip(*candles, strict=True) if candles else ((), (), ())
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 0.0},
        index=index,
    )


#: Vela 1 del oro y la vela 2 que abre el rango bajista.
VELA_1 = (1922.0, 1892.0, 1919.0)
BARRIDO_ARRIBA = (1929.0, 1910.5, 1913.5)


def test_sacar_el_maximo_y_cerrar_dentro_abre_un_rango_bajista() -> None:
    rangos = crt_ranges(_bars(VELA_1, BARRIDO_ARRIBA))
    assert len(rangos) == 1
    rango = rangos.iloc[0]
    assert rango["direction"] == BEARISH
    assert (rango["reference"], rango["confirmation"]) == (0, 1)
    assert (rango["high"], rango["low"]) == (1922.0, 1892.0)
    assert rango["end"] == -1
    assert rango["end_reason"] == ""


def test_sacar_el_minimo_y_cerrar_dentro_abre_un_rango_alcista() -> None:
    rangos = crt_ranges(_bars((110.0, 100.0, 105.0), (108.0, 98.0, 103.0)))
    assert rangos["direction"].tolist() == [BULLISH]


def test_sacar_los_dos_extremos_no_abre_nada() -> None:
    assert crt_ranges(_bars((110.0, 100.0, 105.0), (112.0, 98.0, 105.0))).empty


def test_cerrar_fuera_es_expansion_y_no_abre_nada() -> None:
    assert crt_ranges(_bars((110.0, 100.0, 105.0), (115.0, 104.0, 113.0))).empty


def test_romper_el_bajo_de_la_anterior_sin_rechazarlo_no_lo_frena() -> None:
    """Imágenes 2 y 3: cada vela rompe el bajo de la anterior y cierra por debajo."""
    rangos = crt_ranges(
        _bars(
            VELA_1,
            BARRIDO_ARRIBA,
            (1914.7, 1899.5, 1904.0),
            (1916.0, 1896.4, 1898.9),
        )
    )
    assert len(rangos) == 1
    assert rangos.iloc[0]["end"] == -1


def test_sacar_el_bajo_de_la_anterior_y_rechazarlo_lo_frena() -> None:
    """El turtle soup: rompe el bajo de la vela ANTERIOR y cierra por encima."""
    rangos = crt_ranges(
        _bars(
            VELA_1,
            BARRIDO_ARRIBA,
            (1914.7, 1899.5, 1904.0),
            (1906.0, 1897.0, 1905.0),
        )
    )
    assert rangos.iloc[0]["end"] == 3
    assert rangos.iloc[0]["end_reason"] == REJECTED


def test_sin_rechazo_el_turtle_soup_no_lo_frena_ni_abre_el_contrario() -> None:
    """H3: el turtle soup a la vela anterior no cuenta; el alcista que forma se ignora."""
    rangos = crt_ranges(
        _bars(
            VELA_1,
            BARRIDO_ARRIBA,
            (1914.7, 1899.5, 1904.0),
            (1908.0, 1897.0, 1905.0),
        ),
        rejection=False,
    )
    assert rangos["direction"].tolist() == [BEARISH]
    assert rangos.iloc[0]["end"] == -1


def test_la_vela_que_lo_frena_abre_el_rango_contrario_si_cumple() -> None:
    """Saca el bajo de la anterior y cierra DENTRO de ella: muere el bajista, nace el alcista."""
    rangos = crt_ranges(
        _bars(
            VELA_1,
            BARRIDO_ARRIBA,
            (1914.7, 1899.5, 1904.0),
            (1908.0, 1897.0, 1905.0),
        )
    )
    assert rangos["direction"].tolist() == [BEARISH, BULLISH]
    assert rangos.iloc[0]["end"] == 3
    assert rangos.iloc[1]["confirmation"] == 3
    assert (rangos.iloc[1]["high"], rangos.iloc[1]["low"]) == (1914.7, 1899.5)


def test_cerrar_por_encima_del_maximo_de_la_vela_1_lo_frena() -> None:
    rangos = crt_ranges(_bars(VELA_1, BARRIDO_ARRIBA, (1930.0, 1911.0, 1925.0)))
    assert rangos.iloc[0]["end"] == 2
    assert rangos.iloc[0]["end_reason"] == CLOSED_BEYOND


def test_tocar_el_minimo_de_la_vela_1_lo_termina() -> None:
    rangos = crt_ranges(
        _bars(VELA_1, BARRIDO_ARRIBA, (1912.0, 1899.0, 1900.0), (1901.0, 1892.0, 1893.0))
    )
    assert rangos.iloc[0]["end"] == 3
    assert rangos.iloc[0]["end_reason"] == TARGET_HIT


def test_otro_rango_del_mismo_sentido_con_uno_vivo_se_ignora() -> None:
    rangos = crt_ranges(
        _bars(
            VELA_1,
            BARRIDO_ARRIBA,
            (1912.0, 1905.0, 1906.0),
            (1914.0, 1906.0, 1910.0),  # saca el máximo de la anterior y cierra dentro
        )
    )
    assert len(rangos) == 1
    assert rangos.iloc[0]["end"] == -1


def test_muerto_el_rango_puede_nacer_otro_mas_adelante() -> None:
    rangos = crt_ranges(
        _bars(
            VELA_1,
            BARRIDO_ARRIBA,
            (1932.0, 1911.0, 1930.0),  # cierra fuera: muere, y es expansión: no abre
            (1933.0, 1920.0, 1928.0),  # saca el máximo y cierra dentro: nace otro
        )
    )
    assert rangos["confirmation"].tolist() == [1, 3]
    assert rangos.iloc[1]["direction"] == BEARISH


def test_la_vela_que_lo_confirma_no_puede_frenarlo() -> None:
    """Los frenos se cuentan desde la vela SIGUIENTE a la que lo confirma."""
    rangos = crt_ranges(_bars(VELA_1, BARRIDO_ARRIBA))
    assert rangos.iloc[0]["end"] == -1


def test_serie_vacia_y_una_sola_vela_no_dan_rangos() -> None:
    assert crt_ranges(_bars()).empty
    assert crt_ranges(_bars(VELA_1)).empty


def test_sin_look_ahead_los_rangos_de_un_tramo_son_los_del_historico_completo() -> None:
    velas = _bars(
        VELA_1,
        BARRIDO_ARRIBA,
        (1914.7, 1899.5, 1904.0),
        (1908.0, 1897.0, 1905.0),
        (1909.0, 1900.0, 1907.0),
        (1915.0, 1901.0, 1903.0),
    )
    completo = crt_ranges(velas)
    for corte in range(len(velas) + 1):
        truncado = crt_ranges(velas.iloc[:corte])
        conocido = completo[completo["confirmation"] < corte].copy()
        # Lo que terminó después del corte, visto desde el corte sigue vivo.
        despues = conocido["end"] >= corte
        conocido.loc[despues, "end"] = -1
        conocido.loc[despues, "end_reason"] = ""
        pd.testing.assert_frame_equal(
            truncado.reset_index(drop=True), conocido.reset_index(drop=True)
        )
