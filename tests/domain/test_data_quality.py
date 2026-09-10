"""Los días que faltan en el histórico diario.

Las sesiones son las del corte de las 17:00 de Nueva York: la semana abre el
domingo por la tarde y cierra el viernes por la tarde, así que las aperturas que
se esperan son las de domingo a jueves —hora de la plaza— y ninguna más.
"""

from __future__ import annotations

from datetime import time

import pandas as pd

from chronos.domain.crt.data_quality import daily_coverage

NY = "America/New_York"
OPENS_AT = time(17, 0)

#: Dos semanas de sesiones: domingo a jueves, sin viernes ni sábado.
FULL_WEEKS = [
    "2024-01-07", "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
    "2024-01-14", "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18",
]


def _sessions(days: list[str]) -> pd.DatetimeIndex:
    """Aperturas de sesión en UTC, como las etiqueta la agregación diaria."""
    naive = pd.DatetimeIndex([pd.Timestamp(f"{day} 17:00") for day in days])
    return naive.tz_localize(NY).tz_convert("UTC")


def test_un_historico_completo_no_tiene_dias_sueltos() -> None:
    coverage = daily_coverage(_sessions(FULL_WEEKS), NY, OPENS_AT)

    assert coverage.ok
    assert coverage.missing == ()
    assert coverage.present == coverage.expected == len(FULL_WEEKS)


def test_el_fin_de_semana_no_cuenta_como_dia_que_falta() -> None:
    """Entre el jueves y el domingo siguiente no hay sesión que echar de menos."""
    coverage = daily_coverage(_sessions(FULL_WEEKS), NY, OPENS_AT)
    # Diez sesiones en catorce días naturales: los dos viernes y los dos sábados
    # no se esperan.
    assert coverage.expected == 10


def test_un_dia_que_falta_se_nombra() -> None:
    sin_martes = [day for day in FULL_WEEKS if day != "2024-01-09"]
    coverage = daily_coverage(_sessions(sin_martes), NY, OPENS_AT)

    assert not coverage.ok
    assert len(coverage.missing) == 1
    assert coverage.missing[0] == pd.Timestamp("2024-01-09 22:00", tz="UTC")
    assert coverage.missing_by_year() == {2024: 1}


def test_lo_que_falta_por_los_bordes_no_se_inventa() -> None:
    """El informe va del primer día que hay al último: antes y después no se opina."""
    coverage = daily_coverage(_sessions(FULL_WEEKS[2:]), NY, OPENS_AT)

    assert coverage.ok
    assert coverage.first == pd.Timestamp("2024-01-09 22:00", tz="UTC")


def test_sin_velas_no_hay_nada_que_comparar() -> None:
    coverage = daily_coverage(pd.DatetimeIndex([], tz="UTC"), NY, OPENS_AT)

    assert coverage.first is None
    assert coverage.expected == 0
    assert coverage.describe() == "sin ninguna vela diaria"
