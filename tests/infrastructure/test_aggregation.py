"""H12: la sesión partida en dos, igual que H4 la parte en seis.

Con el día anclado a las 17:00 de Nueva York, las velas H12 abren a las 17:00 y
a las 05:00 locales todo el año. En UTC eso se mueve una hora dos veces al año,
y el día del cambio de reloj la segunda mitad dura 11 o 13 horas, no 12.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from chronos.application.chart.config import DAILY, H12, AggregationConfig
from chronos.infrastructure.market.aggregation import aggregate, session_buckets


def _m1(start: str, end: str) -> pd.DataFrame:
    index = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    price = 100.0 + np.arange(len(index), dtype=float) / 1000.0
    return pd.DataFrame(
        {"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 1.0},
        index=index,
    )


def test_con_ancla_de_ny_h12_abre_a_las_17_y_a_las_05_locales() -> None:
    # Enero, sin horario de verano: NY = UTC-5, así que 17:00 NY son las 22:00 UTC.
    frame = _m1("2024-01-08 22:00", "2024-01-10 22:00")

    series = aggregate(frame, H12, AggregationConfig(d_session_start="NY_17:00"))

    labels = list(series.frame.index)
    assert labels == [
        pd.Timestamp("2024-01-08 22:00", tz="UTC"),
        pd.Timestamp("2024-01-09 10:00", tz="UTC"),
        pd.Timestamp("2024-01-09 22:00", tz="UTC"),
        pd.Timestamp("2024-01-10 10:00", tz="UTC"),
    ]
    assert "troceada cada 12 h" in series.description


def test_dos_velas_h12_hacen_la_vela_diaria() -> None:
    frame = _m1("2024-01-08 22:00", "2024-01-10 22:00")
    config = AggregationConfig(d_session_start="NY_17:00")

    half = aggregate(frame, H12, config).frame
    daily = aggregate(frame, DAILY, config).frame

    for position, day in enumerate(daily.index):
        pair = half.iloc[2 * position : 2 * position + 2]
        assert pair.index[0] == day
        assert pair["open"].iloc[0] == daily.loc[day, "open"]
        assert pair["close"].iloc[-1] == daily.loc[day, "close"]
        assert pair["high"].max() == daily.loc[day, "high"]
        assert pair["low"].min() == daily.loc[day, "low"]


def test_el_dia_del_cambio_de_hora_la_segunda_mitad_dura_11() -> None:
    """El 10 de marzo de 2024 Nueva York adelanta el reloj: la sesión dura 23 h."""
    anchor = AggregationConfig(d_session_start="NY_17:00").session_anchor
    assert anchor is not None
    # Sesión del sábado 9 a las 17:00 EST (22:00 UTC) al domingo 10 a las 17:00
    # EDT (21:00 UTC).
    stamps = pd.DatetimeIndex(
        ["2024-03-09 22:00", "2024-03-10 10:00", "2024-03-10 20:59"], tz="UTC"
    )

    labels, ends = session_buckets(stamps, H12, anchor)

    assert list(labels) == [
        pd.Timestamp("2024-03-09 22:00", tz="UTC"),
        pd.Timestamp("2024-03-10 10:00", tz="UTC"),
        pd.Timestamp("2024-03-10 10:00", tz="UTC"),
    ]
    assert ends[1] == pd.Timestamp("2024-03-10 21:00", tz="UTC")  # 11 h


def test_sin_ancla_h12_parte_el_dia_por_su_hora_de_arranque() -> None:
    frame = _m1("2024-01-08 22:00", "2024-01-09 22:00")

    series = aggregate(frame, H12, AggregationConfig(d_session_start="22:00"))

    assert list(series.frame.index) == [
        pd.Timestamp("2024-01-08 22:00", tz="UTC"),
        pd.Timestamp("2024-01-09 10:00", tz="UTC"),
    ]
