"""La alineación de H4 con el sesgo diario, sobre rangos escritos a mano.

Los rangos se construyen aquí directamente en vez de detectarlos: lo que se
comprueba no es la detección —eso es cosa de `test_ranges`— sino el CRUCE de
dos ciclos de vida ya resueltos. Escribirlos a mano permite además poner el
caso exacto que interesa: dos rangos H4 vivos a la vez y en direcciones
opuestas, que en una serie de velas sintéticas costaría media pantalla.

Todos los instantes son CIERRES de vela. Es la regla del módulo y también la de
estos tests: un rango se conoce cuando cierra la vela que lo confirmó, no cuando
esa vela abre.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.domain.crt.alignment import (
    COLUMNS,
    H4_ALINEADO,
    H4_EN_CONTRA,
    IGNORED,
    LATEST,
    SIN_TARGET,
    STRICT,
    TARGET_DEFINIDO,
    alignment_delays,
    compute_state_h4,
    mark_time_filter,
    summarize_states,
)
from chronos.domain.crt.ranges import (
    ACTIVE,
    BEARISH,
    BULLISH,
    COMPLETED,
    FAILED,
    current_bias,
    empty_ranges,
)
from chronos.domain.crt.timeframes import H4_OPEN_NY
from chronos.domain.errors import DomainError

DAILY = "D"
H4 = "H4"

#: Doce cierres de vela H4 seguidos, que son dos sesiones completas. La primera
#: cierra a las 21:00 UTC, que es la vela de las 17:00 de Nueva York.
CLOSES = pd.date_range("2024-03-04 21:00", periods=12, freq="4h", tz="UTC")

#: Cierres de vela diaria, uno por sesión, alineados con los de H4.
DAYS = pd.date_range("2024-03-04 21:00", periods=3, freq="24h", tz="UTC")


def _range(
    range_id: int,
    direction: str,
    known: pd.Timestamp,
    resolved: pd.Timestamp | None = None,
    status: str | None = None,
    target: float = 110.0,
    open_ny: str = "17:00",
) -> dict[str, object]:
    """Un rango ya resuelto, con las dos marcas que importan: sus dos CIERRES."""
    return {
        "range_id": range_id,
        "direction": direction,
        "target": target,
        "invalidation": target - 20.0,
        "status": status or (ACTIVE if resolved is None else COMPLETED),
        "t_confirm_close": known,
        "t_resolved_close": pd.NaT if resolved is None else resolved,
        H4_OPEN_NY: open_ny,
    }


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Los rangos como DataFrame, con los tipos con los que viajan de verdad."""
    if not rows:
        frame = empty_ranges()
        frame["t_confirm_close"] = pd.Series(dtype="datetime64[ns, UTC]")
        frame["t_resolved_close"] = pd.Series(dtype="datetime64[ns, UTC]")
        frame[H4_OPEN_NY] = pd.Series(dtype="object")
        return frame
    frame = pd.DataFrame(rows)
    frame["t_confirm_close"] = pd.DatetimeIndex(frame["t_confirm_close"], tz="UTC")
    frame["t_resolved_close"] = pd.DatetimeIndex(frame["t_resolved_close"], tz="UTC")
    return frame


def _states(frame: pd.DataFrame) -> list[str]:
    return [str(value) for value in frame["state"]]


#: Un rango diario ALCISTA vivo desde el segundo cierre H4 y hasta el final.
D1_BULL = _frame([_range(1, BULLISH, CLOSES[1], target=1912.40)])


# --- Los cuatro estados -----------------------------------------------------------


def test_sin_rango_diario_no_hay_target_aunque_haya_h4() -> None:
    """H4 sin sesgo diario no dice nada: no hay nada hacia lo que ir."""
    h4 = _frame([_range(7, BULLISH, CLOSES[2])])
    estados = compute_state_h4(_frame([]), h4, CLOSES)

    assert set(_states(estados)) == {SIN_TARGET}
    assert estados["bias_d1"].isna().all()
    assert estados["h4_focus_range_id"].isna().all()


def test_con_sesgo_y_sin_rango_h4_el_sistema_espera() -> None:
    estados = compute_state_h4(D1_BULL, _frame([]), CLOSES)

    assert _states(estados)[0] == SIN_TARGET
    assert set(_states(estados)[1:]) == {TARGET_DEFINIDO}
    assert estados["bias_d1"].iloc[1] == BULLISH
    assert estados["d1_target"].iloc[1] == pytest.approx(1912.40)
    assert int(estados["d1_range_id"].iloc[1]) == 1


def test_diario_alcista_con_h4_alcista_queda_alineado() -> None:
    h4 = _frame([_range(7, BULLISH, CLOSES[3])])
    estados = compute_state_h4(D1_BULL, h4, CLOSES)

    assert _states(estados)[2] == TARGET_DEFINIDO
    assert set(_states(estados)[3:]) == {H4_ALINEADO}
    assert int(estados["h4_focus_range_id"].iloc[3]) == 7
    assert estados["h4_blocking_range_id"].isna().all()


def test_diario_alcista_con_h4_bajista_queda_bloqueado() -> None:
    h4 = _frame([_range(37, BEARISH, CLOSES[3], target=1884.20)])
    estados = compute_state_h4(D1_BULL, h4, CLOSES)

    assert set(_states(estados)[3:]) == {H4_EN_CONTRA}
    assert int(estados["h4_blocking_range_id"].iloc[3]) == 37
    assert estados["h4_focus_range_id"].isna().all()


def test_cuando_el_h4_en_contra_se_completa_el_sistema_vuelve_a_esperar() -> None:
    """Resuelto el rango que bloqueaba y sin otro vivo, se espera de nuevo."""
    h4 = _frame([_range(37, BEARISH, CLOSES[3], resolved=CLOSES[6], status=COMPLETED)])
    estados = compute_state_h4(D1_BULL, h4, CLOSES)

    assert _states(estados)[5] == H4_EN_CONTRA
    assert set(_states(estados)[6:]) == {TARGET_DEFINIDO}


def test_un_h4_fallido_tambien_suelta_el_bloqueo() -> None:
    h4 = _frame([_range(37, BEARISH, CLOSES[3], resolved=CLOSES[5], status=FAILED)])
    estados = compute_state_h4(D1_BULL, h4, CLOSES)

    assert _states(estados)[4] == H4_EN_CONTRA
    assert _states(estados)[5] == TARGET_DEFINIDO


# --- Los dos modos ----------------------------------------------------------------


def _dos_rangos_opuestos() -> pd.DataFrame:
    """Un H4 bajista vivo y, después, uno alcista. Los dos siguen vivos."""
    return _frame(
        [
            _range(37, BEARISH, CLOSES[3], target=1884.20),
            _range(38, BULLISH, CLOSES[6], target=1930.00),
        ]
    )


def test_en_modo_latest_manda_el_h4_mas_reciente() -> None:
    estados = compute_state_h4(D1_BULL, _dos_rangos_opuestos(), CLOSES, alignment_mode=LATEST)

    assert _states(estados)[5] == H4_EN_CONTRA
    assert set(_states(estados)[6:]) == {H4_ALINEADO}
    assert int(estados["h4_focus_range_id"].iloc[6]) == 38


def test_en_modo_strict_el_bajista_vivo_sigue_bloqueando() -> None:
    estados = compute_state_h4(D1_BULL, _dos_rangos_opuestos(), CLOSES, alignment_mode=STRICT)

    assert set(_states(estados)[3:]) == {H4_EN_CONTRA}
    assert int(estados["h4_blocking_range_id"].iloc[6]) == 37


def test_en_modo_strict_se_alinea_cuando_muere_el_que_iba_en_contra() -> None:
    h4 = _frame(
        [
            _range(37, BEARISH, CLOSES[3], resolved=CLOSES[8], status=FAILED),
            _range(38, BULLISH, CLOSES[6]),
        ]
    )
    estados = compute_state_h4(D1_BULL, h4, CLOSES, alignment_mode=STRICT)

    assert _states(estados)[7] == H4_EN_CONTRA
    assert set(_states(estados)[8:]) == {H4_ALINEADO}
    assert int(estados["h4_focus_range_id"].iloc[8]) == 38


def test_un_modo_que_no_existe_falla_en_vez_de_elegir_uno() -> None:
    with pytest.raises(DomainError, match="alignment_mode"):
        compute_state_h4(D1_BULL, _frame([]), CLOSES, alignment_mode="el que sea")


# --- Quién manda cuando hay varios vivos -------------------------------------------


def test_al_morir_el_mas_reciente_vuelve_a_mandar_el_anterior() -> None:
    """Dos H4 alcistas vivos: muere el nuevo y el viejo sigue enfocando."""
    h4 = _frame(
        [
            _range(1, BULLISH, CLOSES[2]),
            _range(2, BEARISH, CLOSES[4], resolved=CLOSES[7], status=FAILED),
        ]
    )
    estados = compute_state_h4(D1_BULL, h4, CLOSES, alignment_mode=LATEST)

    assert _states(estados)[3] == H4_ALINEADO
    assert _states(estados)[4] == H4_EN_CONTRA
    assert _states(estados)[7] == H4_ALINEADO
    assert int(estados["h4_focus_range_id"].iloc[7]) == 1


def test_el_sesgo_diario_es_el_mismo_que_calcula_el_paso_anterior() -> None:
    """El cruce no puede reescribir la regla del sesgo: se comprueba contra ella.

    `current_bias` lee etiquetas de vela y esto lee cierres; con los rangos
    entrando por su cierre las dos respuestas tienen que coincidir vela a vela.
    """
    ranges = _frame(
        [
            _range(1, BULLISH, CLOSES[1], resolved=CLOSES[5], status=COMPLETED),
            _range(2, BEARISH, CLOSES[3]),
            _range(3, BULLISH, CLOSES[8], resolved=CLOSES[10], status=FAILED),
        ]
    )
    espejo = ranges.copy()
    espejo["t_confirm"] = espejo["t_confirm_close"]
    espejo["t_resolved"] = espejo["t_resolved_close"]

    estados = compute_state_h4(ranges, _frame([]), CLOSES)
    for position, moment in enumerate(CLOSES):
        esperado = current_bias(espejo, as_of=moment)
        obtenido = estados.iloc[position]
        if "range_id" not in esperado:
            assert pd.isna(obtenido["d1_range_id"])
        else:
            assert int(obtenido["d1_range_id"]) == esperado["range_id"]
            assert obtenido["bias_d1"] == esperado["direction"]


# --- Sin lookahead ------------------------------------------------------------------


def test_ningun_rango_cuenta_antes_de_que_cierre_su_vela() -> None:
    """La fila del cierre `t` conoce el rango confirmado EN `t`, y ninguno más."""
    h4 = _frame([_range(7, BULLISH, CLOSES[4])])
    estados = compute_state_h4(D1_BULL, h4, CLOSES)

    assert _states(estados)[3] == TARGET_DEFINIDO
    assert _states(estados)[4] == H4_ALINEADO


def test_truncar_el_historico_no_cambia_lo_que_ya_se_sabia() -> None:
    """La prueba de no mirar al futuro: mismas filas con menos velas por delante."""
    h4 = _dos_rangos_opuestos()
    completo = compute_state_h4(D1_BULL, h4, CLOSES)
    recortado = compute_state_h4(D1_BULL, h4, CLOSES[:7])

    pd.testing.assert_frame_equal(completo.iloc[:7], recortado)


def test_los_cierres_tienen_que_ir_en_orden() -> None:
    with pytest.raises(DomainError, match="en orden"):
        compute_state_h4(D1_BULL, _frame([]), CLOSES[::-1])


def test_sin_los_cierres_de_vela_no_se_calcula_nada() -> None:
    """Con la etiqueta de la vela en vez de su cierre esto miraría al futuro."""
    sin_cierres = D1_BULL.drop(columns=["t_confirm_close"])
    with pytest.raises(DomainError, match="t_confirm_close"):
        compute_state_h4(sin_cierres, _frame([]), CLOSES)


# --- El filtro horario ---------------------------------------------------------------


def test_sin_filtro_no_se_ignora_ningun_rango() -> None:
    marcados = mark_time_filter(_dos_rangos_opuestos())
    assert not marcados[IGNORED].any()


def test_el_filtro_marca_los_rangos_de_las_velas_que_no_son_clave() -> None:
    h4 = _frame(
        [
            _range(1, BULLISH, CLOSES[2], open_ny="21:00"),
            _range(2, BULLISH, CLOSES[4], open_ny="05:00"),
        ]
    )
    marcados = mark_time_filter(h4, ["01:00", "05:00", "09:00"])

    assert list(marcados[IGNORED]) == [True, False]


def test_un_rango_ignorado_no_alinea_ni_bloquea() -> None:
    h4 = _frame([_range(1, BULLISH, CLOSES[2], open_ny="21:00")])
    estados = compute_state_h4(D1_BULL, h4, CLOSES, key_h4_opens_ny=["05:00"])

    assert set(_states(estados)[1:]) == {TARGET_DEFINIDO}


def test_el_filtro_horario_exige_saber_a_que_hora_abrio_la_vela() -> None:
    h4 = _dos_rangos_opuestos().drop(columns=[H4_OPEN_NY])
    with pytest.raises(DomainError, match=H4_OPEN_NY):
        compute_state_h4(D1_BULL, h4, CLOSES, key_h4_opens_ny=["05:00"])


# --- El resumen ------------------------------------------------------------------------


def test_el_resumen_reparte_el_tiempo_entre_los_cuatro_estados() -> None:
    estados = compute_state_h4(D1_BULL, _dos_rangos_opuestos(), CLOSES)
    resumen = summarize_states(estados)

    assert resumen.total == len(CLOSES)
    assert sum(resumen.counts.values()) == len(CLOSES)
    assert resumen.share(SIN_TARGET) == pytest.approx(100.0 / len(CLOSES))


def test_el_retraso_cuenta_las_velas_hasta_la_primera_alineada() -> None:
    """Cuántas velas H4 se pasó esperando el rango diario que sí se completó."""
    d1 = _frame([_range(1, BULLISH, CLOSES[1], resolved=CLOSES[10], status=COMPLETED)])
    h4 = _frame([_range(7, BULLISH, CLOSES[5])])
    estados = compute_state_h4(d1, h4, CLOSES)

    retrasos = alignment_delays(estados, d1)
    assert retrasos.total == 1
    assert retrasos.delays[0].range_id == 1
    assert retrasos.delays[0].bars == 4
    assert retrasos.never == 0


def test_un_rango_diario_que_nunca_se_alinea_se_cuenta_aparte() -> None:
    d1 = _frame([_range(1, BULLISH, CLOSES[1], resolved=CLOSES[10], status=COMPLETED)])
    h4 = _frame([_range(7, BEARISH, CLOSES[5])])
    estados = compute_state_h4(d1, h4, CLOSES)

    retrasos = alignment_delays(estados, d1)
    assert retrasos.never == 1
    assert retrasos.mean_bars is None


def test_los_rangos_diarios_que_no_se_completaron_no_entran_en_el_retraso() -> None:
    d1 = _frame([_range(1, BULLISH, CLOSES[1], resolved=CLOSES[6], status=FAILED)])
    estados = compute_state_h4(d1, _frame([]), CLOSES)

    assert alignment_delays(estados, d1).total == 0


# --- Sin velas ---------------------------------------------------------------------------


def test_sin_velas_h4_no_hay_ni_una_fila() -> None:
    vacio = compute_state_h4(D1_BULL, _frame([]), pd.DatetimeIndex([], tz="UTC"))

    assert vacio.empty
    assert list(vacio.columns) == list(COLUMNS)
    assert summarize_states(vacio).total == 0
    assert alignment_delays(vacio, D1_BULL).total == 0


def test_los_estados_de_los_dias_se_pueden_leer_por_sesion() -> None:
    """El índice es el cierre de cada vela H4: reindexar por día es cosa de quien mire."""
    estados = compute_state_h4(D1_BULL, _dos_rangos_opuestos(), CLOSES)

    ultimo_del_dia = estados["state"].reindex(DAYS[:2], method="ffill")
    assert list(ultimo_del_dia) == [SIN_TARGET, H4_ALINEADO]
