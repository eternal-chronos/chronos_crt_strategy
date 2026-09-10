"""Los rangos CRT sobre series sintéticas cortas.

Cada caso es una serie de cinco a ocho velas escrita a mano, con los números
elegidos para que la regla se pueda comprobar leyendo: la vela de referencia
marca el rango [90, 110], la siguiente le saca un extremo y cierra dentro, y las
de después lo resuelven. Sin mocks y sin ficheros: son funciones puras sobre
arrays.

Las velas de relleno REPITEN el máximo y el mínimo de la anterior. Como las dos
tomas se miden con desigualdad estricta, una vela así no le saca ningún extremo
a la anterior y por tanto no puede abrir un rango sin querer, que es lo que
ensuciaría el recuento de cada caso.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.domain.crt.ranges import (
    ACTIVE,
    BEARISH,
    BULLISH,
    COLUMNS,
    COMPLETED,
    EXPIRED,
    FAILED,
    NO_TARGET,
    RangeParams,
    current_bias,
    detect_ranges,
    summarize,
)
from chronos.domain.errors import DomainError

DAILY = "D"

Candle = tuple[float, float, float, float]


def _frame(rows: list[Candle], start: str = "2024-01-02") -> pd.DataFrame:
    """Velas diarias a partir de tuplas (open, high, low, close)."""
    index = pd.date_range(start=start, periods=len(rows), freq="1D", tz="UTC")
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    frame["volume"] = 100.0
    frame.index.name = "timestamp"
    return frame


def _quiet(previous: Candle) -> Candle:
    """Vela que repite los extremos de la anterior: no le saca ninguno."""
    high, low = previous[1], previous[2]
    middle = (high + low) / 2
    return (middle, high, low, middle)


#: La primera vela de todas las series. CONTIENE a la de referencia, así que la
#: referencia no abre ningún rango contra ella y el caso empieza limpio.
OPENING: Candle = (100.0, 120.0, 80.0, 100.0)

#: La vela de referencia: rango [90, 110].
REFERENCE: Candle = (95.0, 110.0, 90.0, 105.0)

#: Le saca el mínimo (85 < 90) y cierra dentro (90 < 100 < 110): rango ALCISTA
#: con objetivo en 110 e invalidación en 85.
BULL_CONFIRM: Candle = (100.0, 106.0, 85.0, 100.0)

#: Le saca el máximo (115 > 110) y cierra dentro: rango BAJISTA con objetivo en
#: 90 e invalidación en 115.
BEAR_CONFIRM: Candle = (105.0, 115.0, 95.0, 100.0)

#: Alcanza el objetivo del rango alcista (112 >= 110) sin abrir ninguno nuevo.
HITS_TARGET: Candle = (100.0, 112.0, 98.0, 108.0)


# --- Detección ------------------------------------------------------------------


def test_rango_alcista_toma_el_minimo_y_cierra_dentro() -> None:
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, _quiet(BULL_CONFIRM)])
    ranges = detect_ranges(frame, DAILY)

    assert len(ranges) == 1
    rango = ranges.iloc[0]
    assert rango["direction"] == BULLISH
    assert rango["tf"] == DAILY
    assert (rango["range_high"], rango["range_low"]) == (110.0, 90.0)
    assert rango["manip_extreme"] == 85.0
    assert rango["target"] == 110.0
    assert rango["invalidation"] == 85.0
    assert rango["size"] == 20.0
    # El rango nace con la vela que lo confirma, no con la de referencia.
    assert rango["t_ref_open"] == frame.index[1]
    assert rango["t_confirm"] == frame.index[2]


def test_rango_bajista_toma_el_maximo_y_cierra_dentro() -> None:
    frame = _frame([OPENING, REFERENCE, BEAR_CONFIRM, _quiet(BEAR_CONFIRM)])
    ranges = detect_ranges(frame, DAILY)

    assert len(ranges) == 1
    rango = ranges.iloc[0]
    assert rango["direction"] == BEARISH
    assert rango["manip_extreme"] == 115.0
    assert rango["target"] == 90.0
    assert rango["invalidation"] == 115.0


def test_la_vela_que_toma_los_dos_extremos_es_ambigua_y_no_es_rango() -> None:
    """Con las dos manipulaciones dentro no se sabe cuál manda: en esta versión, nada."""
    ambigua: Candle = (100.0, 115.0, 85.0, 100.0)
    frame = _frame([OPENING, REFERENCE, ambigua, _quiet(ambigua)])
    assert detect_ranges(frame, DAILY).empty


def test_la_expansion_no_es_rango() -> None:
    """Toma un extremo y cierra FUERA por el otro lado: eso es expansión."""
    rompe_abajo: Candle = (100.0, 106.0, 85.0, 88.0)   # saca el mínimo y cierra debajo
    rompe_arriba: Candle = (105.0, 115.0, 95.0, 112.0)  # saca el máximo y cierra encima
    abajo = _frame([OPENING, REFERENCE, rompe_abajo, _quiet(rompe_abajo)])
    arriba = _frame([OPENING, REFERENCE, rompe_arriba, _quiet(rompe_arriba)])
    assert detect_ranges(abajo, DAILY).empty
    assert detect_ranges(arriba, DAILY).empty


def test_cerrar_justo_en_el_extremo_no_es_cerrar_dentro() -> None:
    """`low[i-1] < close[i] < high[i-1]` es estricto por los dos lados."""
    en_el_borde: Candle = (100.0, 106.0, 85.0, 90.0)
    frame = _frame([OPENING, REFERENCE, en_el_borde, _quiet(en_el_borde)])
    assert detect_ranges(frame, DAILY).empty


def test_sin_velas_suficientes_no_hay_nada_que_detectar() -> None:
    vacio = detect_ranges(_frame([]), DAILY)
    assert vacio.empty
    assert tuple(vacio.columns) == COLUMNS
    assert detect_ranges(_frame([REFERENCE]), DAILY).empty


# --- Ciclo de vida ---------------------------------------------------------------


def test_completado_en_la_primera_vela_que_alcanza_el_objetivo() -> None:
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, _quiet(BULL_CONFIRM), HITS_TARGET])
    ranges = detect_ranges(frame, DAILY)

    assert len(ranges) == 1
    rango = ranges.iloc[0]
    assert rango["status"] == COMPLETED
    assert rango["t_resolved"] == frame.index[4]
    assert rango["candles_to_resolve"] == 2
    assert not rango["ambiguous_resolution"]


def test_fallido_en_la_primera_vela_que_pierde_la_invalidacion() -> None:
    pierde: Candle = (100.0, 104.0, 84.0, 84.5)  # low 84 < 85
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, pierde])
    ranges = detect_ranges(frame, DAILY)

    assert len(ranges) == 1
    rango = ranges.iloc[0]
    assert rango["status"] == FAILED
    assert rango["t_resolved"] == frame.index[3]
    assert rango["candles_to_resolve"] == 1


def test_la_vela_que_hace_las_dos_cosas_se_resuelve_como_fallida() -> None:
    """Criterio conservador: dentro de una vela no se sabe qué pasó primero."""
    las_dos: Candle = (100.0, 112.0, 84.0, 100.0)
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, las_dos])
    rango = detect_ranges(frame, DAILY).iloc[0]

    assert rango["status"] == FAILED
    assert rango["ambiguous_resolution"]
    assert rango["t_resolved"] == frame.index[3]


def test_el_rango_que_no_se_resuelve_sigue_activo() -> None:
    reposo = _quiet(BULL_CONFIRM)
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, reposo, _quiet(reposo)])
    rango = detect_ranges(frame, DAILY).iloc[0]

    assert rango["status"] == ACTIVE
    assert pd.isna(rango["t_resolved"])
    assert pd.isna(rango["candles_to_resolve"])


def test_max_candles_alive_expira_el_rango_que_aguanta_de_mas() -> None:
    reposo = _quiet(BULL_CONFIRM)
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, reposo, reposo, reposo])
    rango = detect_ranges(frame, DAILY, max_candles_alive=2).iloc[0]

    assert rango["status"] == EXPIRED
    assert rango["t_resolved"] == frame.index[4]
    assert rango["candles_to_resolve"] == 2
    # Sin límite el mismo rango sigue vivo.
    assert detect_ranges(frame, DAILY).iloc[0]["status"] == ACTIVE


def test_un_objetivo_alcanzado_dentro_del_plazo_no_expira() -> None:
    frame = _frame(
        [OPENING, REFERENCE, BULL_CONFIRM, HITS_TARGET, _quiet(HITS_TARGET)]
    )
    assert detect_ranges(frame, DAILY, max_candles_alive=2).iloc[0]["status"] == COMPLETED


def test_el_bajista_se_completa_hacia_abajo() -> None:
    llega: Candle = (100.0, 104.0, 88.0, 92.0)  # low 88 <= 90
    frame = _frame([OPENING, REFERENCE, BEAR_CONFIRM, llega])
    ranges = detect_ranges(frame, DAILY)

    assert len(ranges) == 1
    assert ranges.iloc[0]["status"] == COMPLETED
    assert ranges.iloc[0]["t_resolved"] == frame.index[3]


# --- Varios rangos a la vez -------------------------------------------------------


def test_puede_haber_varios_rangos_vivos_y_manda_el_ultimo() -> None:
    """El segundo rango nace sin tocar el objetivo ni la invalidación del primero."""
    segunda_referencia = _quiet(BULL_CONFIRM)          # rango [85, 106]
    segunda_confirmacion: Candle = (100.0, 108.0, 95.0, 100.0)  # le saca el máximo
    frame = _frame(
        [OPENING, REFERENCE, BULL_CONFIRM, segunda_referencia, segunda_confirmacion]
    )
    ranges = detect_ranges(frame, DAILY)

    assert list(ranges["direction"]) == [BULLISH, BEARISH]
    assert list(ranges["status"]) == [ACTIVE, ACTIVE]
    sesgo = current_bias(ranges, frame.index[-1])
    assert sesgo["direction"] == BEARISH
    assert sesgo["target"] == 85.0
    assert sesgo["invalidation"] == 108.0
    assert sesgo["range_id"] == 2


def test_sin_ningun_rango_vivo_no_hay_sesgo() -> None:
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, _quiet(BULL_CONFIRM)])
    ranges = detect_ranges(frame, DAILY)

    # Antes de que la vela de confirmación cerrara todavía no se sabía nada.
    assert current_bias(ranges, frame.index[1])["direction"] == NO_TARGET
    assert current_bias(detect_ranges(_frame([OPENING, OPENING]), DAILY), frame.index[1]) == {
        "direction": NO_TARGET
    }


def test_el_sesgo_se_apaga_cuando_el_rango_se_resuelve() -> None:
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, HITS_TARGET])
    ranges = detect_ranges(frame, DAILY)

    assert current_bias(ranges, frame.index[2])["direction"] == BULLISH
    assert current_bias(ranges, frame.index[3])["direction"] == NO_TARGET


def test_el_sesgo_no_mira_al_futuro() -> None:
    """Lo que se sabía el día 2 no cambia porque después pase nada más.

    Es el test de no-look-ahead: detectar sobre el histórico truncado y sobre el
    completo tiene que dar exactamente el mismo sesgo a esa fecha.
    """
    completo = _frame(
        [OPENING, REFERENCE, BULL_CONFIRM, HITS_TARGET, _quiet(HITS_TARGET)]
    )
    corte = completo.index[2]
    truncado = completo.loc[:corte]

    assert current_bias(detect_ranges(truncado, DAILY), corte) == current_bias(
        detect_ranges(completo, DAILY), corte
    )
    # Y la detección de lo ya confirmado es la misma fila en los dos.
    columnas = ["direction", "t_ref_open", "t_confirm", "target", "invalidation"]
    pd.testing.assert_frame_equal(
        detect_ranges(truncado, DAILY)[columnas],
        detect_ranges(completo, DAILY)[columnas].iloc[:1],
    )


# --- Filtro de ruido ---------------------------------------------------------------


#: Velas iguales de rango 1,00: el ATR(14) vale exactamente 1,00 sobre ellas y
#: ninguna le saca un extremo a la anterior.
FLAT: Candle = (100.0, 100.5, 99.5, 100.0)

#: Referencia de rango 3,00 sobre ese ATR, y su confirmación alcista.
WIDE_REFERENCE: Candle = (100.0, 102.0, 99.0, 100.0)
WIDE_CONFIRM: Candle = (100.0, 101.0, 98.0, 100.5)


def _wide_after_warmup() -> pd.DataFrame:
    return _frame([*([FLAT] * 15), WIDE_REFERENCE, WIDE_CONFIRM, _quiet(WIDE_CONFIRM)])


def test_el_filtro_de_tamano_esta_apagado_por_defecto() -> None:
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, _quiet(BULL_CONFIRM)])
    ranges = detect_ranges(frame, DAILY)

    assert len(ranges) == 1
    # Sin ATR todavía —las primeras trece velas no tienen— el tamaño en ATR no
    # se puede medir, y aun así el rango se detecta: el filtro está apagado.
    assert pd.isna(ranges.iloc[0]["size_atr"])


def test_el_tamano_se_mide_en_atr_de_la_vela_de_referencia() -> None:
    rango = detect_ranges(_wide_after_warmup(), DAILY).iloc[0]

    assert rango["size"] == 3.0
    # ATR(14) al cerrar la referencia: catorce velas de rango 1,00 y ésta de
    # 3,00, suavizadas por Wilder -> 1,14, así que el rango mide 2,63 ATR.
    assert rango["size_atr"] == pytest.approx(2.625, abs=0.01)


def test_min_size_atr_descarta_los_rangos_pequenos() -> None:
    frame = _wide_after_warmup()
    assert len(detect_ranges(frame, DAILY, min_size_atr=2.0)) == 1
    assert detect_ranges(frame, DAILY, min_size_atr=3.0).empty


def test_con_el_filtro_puesto_un_rango_sin_atr_medible_no_pasa() -> None:
    """No se puede aceptar por tamaño lo que todavía no se puede medir."""
    frame = _frame([OPENING, REFERENCE, BULL_CONFIRM, _quiet(BULL_CONFIRM)])
    assert detect_ranges(frame, DAILY, min_size_atr=0.5).empty


def test_los_parametros_se_validan() -> None:
    with pytest.raises(DomainError, match="min_size_atr"):
        RangeParams(min_size_atr=-1.0)
    with pytest.raises(DomainError, match="max_candles_alive"):
        RangeParams(max_candles_alive=0)
    with pytest.raises(DomainError):
        detect_ranges(_frame([OPENING, REFERENCE]), DAILY, max_candles_alive=0)


# --- Resumen ------------------------------------------------------------------------


def test_el_resumen_cuenta_lo_resuelto_y_lo_que_sigue_vivo() -> None:
    bajista_final: Candle = (105.0, 115.0, 100.0, 105.0)
    frame = _frame(
        [
            OPENING,
            REFERENCE,
            BULL_CONFIRM,
            HITS_TARGET,
            _quiet(HITS_TARGET),
            bajista_final,
        ]
    )
    resumen = summarize(detect_ranges(frame, DAILY), frame.index[-1])

    assert (resumen.total, resumen.bullish, resumen.bearish) == (2, 1, 1)
    assert (resumen.completed, resumen.failed, resumen.expired) == (1, 0, 0)
    assert resumen.resolved == 1
    assert resumen.completed_pct == 100.0
    assert resumen.failed_pct == 0.0
    assert resumen.active == 1
    assert resumen.mean_candles_to_resolve == 1.0


def test_el_resumen_de_una_serie_sin_rangos_no_inventa_porcentajes() -> None:
    resumen = summarize(detect_ranges(_frame([OPENING, OPENING]), DAILY))
    assert resumen.total == 0
    assert resumen.completed_pct is None
    assert resumen.mean_candles_to_resolve is None
