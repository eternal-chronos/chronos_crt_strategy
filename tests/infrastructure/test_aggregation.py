"""Cuándo cierra cada vela agregada.

Las velas van etiquetadas al INICIO de su intervalo, así que su marca no dice
cuándo se supo lo que hay dentro. Todo lo que decide «esto ya se sabía» —el
replay, y con él los rangos CRT— depende de esta cuenta, y con ancla de sesión
no es sumar 24 horas: los dos días del año en que se mueve el reloj de la plaza
la sesión dura 23 o 25.
"""

from __future__ import annotations

import pandas as pd

from chronos.application.chart.config import DAILY, AggregationConfig
from chronos.infrastructure.market.aggregation import bar_closes


def test_con_hora_fija_en_utc_el_dia_dura_siempre_24_horas() -> None:
    config = AggregationConfig(d_session_start="22:00")
    labels = pd.DatetimeIndex(["2024-03-08 22:00", "2024-03-09 22:00"], tz="UTC")

    closes = bar_closes(labels, DAILY, config)

    assert list(closes) == [
        pd.Timestamp("2024-03-09 22:00", tz="UTC"),
        pd.Timestamp("2024-03-10 22:00", tz="UTC"),
    ]


def test_con_ancla_de_sesion_el_dia_del_cambio_de_hora_dura_23() -> None:
    """La sesión abre a la misma hora LOCAL, así que en UTC el corte se mueve.

    El 10 de marzo de 2024 Nueva York adelanta el reloj a las 2:00. La sesión
    que abre ese día a las 00:00 locales cierra 23 horas después, no 24, y
    sumarle un día la dejaría una hora larga.
    """
    config = AggregationConfig(d_session_start="NY_00:00")
    labels = pd.DatetimeIndex(["2024-03-09 05:00", "2024-03-10 05:00"], tz="UTC")

    closes = bar_closes(labels, DAILY, config)

    assert closes[0] == pd.Timestamp("2024-03-10 05:00", tz="UTC")  # 24 h
    assert closes[1] == pd.Timestamp("2024-03-11 04:00", tz="UTC")  # 23 h
