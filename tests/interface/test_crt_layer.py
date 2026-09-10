"""El punto de composición: CUÁNDO se supo cada rango, y quién puede leerlo.

El detector habla de velas —la `i-1`, la `i`, la `j`— y las velas van
etiquetadas al INICIO de su intervalo. Esa etiqueta no dice cuándo el motor
pudo conocer el rango: la vela diaria que abre a las 17:00 lleva marca anterior
a los cierres H4 de las 21:00, la 1:00 y las 5:00, y en ninguno de ellos había
cerrado. Quien resuelve el cierre de verdad es el punto de composición, con la
misma rejilla que agregó las velas.

Aquí se comprueban las dos mitades de esa promesa sobre el histórico entero, no
sobre un caso escrito a mano:

  · que el instante en que un rango EXISTE —`t_confirm_close`— es el cierre de
    la vela que lo confirmó, en las dos temporalidades;
  · que `compute_state_h4` no usa NUNCA un rango en una fila anterior a ese
    cierre, en los dos modos de alineación.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.chart.config import (
    DAILY,
    H4,
    ExplorerConfig,
    ExplorerReportingConfig,
    SymbolConfig,
)
from chronos.domain.crt.alignment import KNOWN_AT, MODES, RESOLVED_AT
from chronos.infrastructure.market.aggregation import aggregate, bar_closes
from chronos.infrastructure.market.chart_run import ChartRun, SymbolBars
from chronos.infrastructure.market.loader import SidedHistory
from chronos.interface.crt_layer import SymbolRanges, build_h4, build_ranges
from tests.conftest import make_m1_history

GOLD = SymbolConfig(symbol="XAUUSD", label="XAUUSD (oro)", bid_path="x.parquet", decimals=2)


@pytest.fixture(scope="module")
def run() -> ChartRun:
    """Doce semanas de M1 sintético agregadas con la rejilla real del proyecto.

    Con el corte anclado a las 17:00 de Nueva York, que es donde la cuenta a
    mano —sumar cuatro o veinticuatro horas— se equivoca: dentro de estas doce
    semanas cae el cambio de reloj de marzo.
    """
    config = ExplorerConfig(
        symbols=(GOLD,),
        reporting=ExplorerReportingConfig(max_explorer_bars=0),
    )
    frame = make_m1_history(weeks=12)
    series = {
        timeframe: aggregate(frame, timeframe, config.aggregation)
        for timeframe in config.ordered_timeframes
    }
    history = SidedHistory(
        frame=frame,
        side=config.price_side,
        provenance="fixture sintética",
        has_bid=True,
        has_ask=False,
    )
    return ChartRun(
        config=config,
        symbols=(SymbolBars(config=GOLD, history=history, series=series, skipped=()),),
        unavailable=(),
    )


@pytest.fixture(scope="module")
def composed(run: ChartRun) -> tuple[SymbolRanges, ...]:
    return build_ranges(run)


def _labels(run: ChartRun) -> dict[str, pd.DatetimeIndex]:
    """Las etiquetas de las velas de cada temporalidad con rangos, del par."""
    item = run.symbols[0]
    return {
        DAILY: pd.DatetimeIndex(item.frames[DAILY].index),
        H4: pd.DatetimeIndex(build_h4(item, run.config.aggregation).index),
    }


def _frames(composed: tuple[SymbolRanges, ...]) -> dict[str, pd.DataFrame]:
    return {DAILY: composed[0].frame, H4: composed[0].h4_frame}


@pytest.mark.parametrize("timeframe", [DAILY, H4])
def test_un_rango_existe_cuando_cierra_la_vela_que_lo_confirma(
    run: ChartRun, composed: tuple[SymbolRanges, ...], timeframe: str
) -> None:
    """Recorre TODOS los rangos de la temporalidad, no una muestra.

    Dos comprobaciones sobre cada uno. La primera es de cableado: el cierre que
    viaja con el rango tiene que ser el de SU vela en SU rejilla —el diario con
    la del día y H4 con la de cuatro horas—, y una temporalidad leída con la
    rejilla de la otra se caería aquí.

    La segunda no depende de esa rejilla y es la que fija el significado: el
    cierre va después de la etiqueta de la vela que confirma y no más allá de
    la etiqueta de la siguiente. Entre las dos no cabe ninguna otra vela, así
    que ese instante sólo puede ser el borde de la vela de confirmación.
    """
    labels = _labels(run)[timeframe]
    frame = _frames(composed)[timeframe]
    assert not frame.empty, f"la fixture tiene que dar rangos de {timeframe}"

    closes = pd.Series(bar_closes(labels, timeframe, run.config.aggregation), index=labels)
    confirm = pd.DatetimeIndex(frame["t_confirm"])
    known = pd.DatetimeIndex(frame[KNOWN_AT])

    assert not known.isna().any(), "un rango sin cierre no se sabe cuándo existió"
    assert list(known) == list(closes.reindex(confirm)), (
        f"el cierre que viaja con los rangos de {timeframe} no es el de su propia rejilla"
    )

    positions = labels.get_indexer(confirm)
    assert (positions >= 0).all(), "toda vela de confirmación está en el índice"
    assert (known.to_numpy() > confirm.to_numpy()).all(), (
        "una vela no puede cerrar antes de abrir"
    )
    # La última vela del histórico no tiene siguiente: de ella sólo se puede
    # decir que cierra después de abrir, y ya se ha dicho.
    tiene_siguiente = positions < len(labels) - 1
    siguiente = labels.to_numpy()[positions[tiene_siguiente] + 1]
    assert (known.to_numpy()[tiene_siguiente] <= siguiente).all(), (
        "el cierre se cuela dentro de la vela siguiente: entre las dos no cabe otra vela"
    )


@pytest.mark.parametrize("timeframe", [DAILY, H4])
def test_un_rango_se_resuelve_cuando_cierra_la_vela_que_lo_resuelve(
    run: ChartRun, composed: tuple[SymbolRanges, ...], timeframe: str
) -> None:
    """Lo mismo por el otro extremo: el rango deja de existir en un CIERRE.

    Los vivos no tienen marca de resolución y se quedan fuera; los resueltos la
    tienen, y tiene que ser posterior a la de su confirmación.
    """
    labels = _labels(run)[timeframe]
    frame = _frames(composed)[timeframe]
    closes = pd.Series(bar_closes(labels, timeframe, run.config.aggregation), index=labels)

    resolved = frame[frame[RESOLVED_AT].notna()]
    assert not resolved.empty, "la fixture tiene que dar rangos ya resueltos"
    assert list(pd.DatetimeIndex(resolved[RESOLVED_AT])) == list(
        closes.reindex(pd.DatetimeIndex(resolved["t_resolved"]))
    )
    assert (
        pd.DatetimeIndex(resolved[RESOLVED_AT]) > pd.DatetimeIndex(resolved[KNOWN_AT])
    ).all(), "un rango no puede resolverse antes de existir"


@pytest.mark.parametrize("mode", MODES)
def test_la_alineacion_no_lee_ningun_rango_antes_de_su_cierre(
    run: ChartRun, composed: tuple[SymbolRanges, ...], mode: str
) -> None:
    """Ni una fila de la línea de estados mira más allá de su propio cierre.

    Es el test de no-look-ahead de la capa: cada fila está indexada por el
    cierre de una vela H4, y los tres rangos a los que puede apuntar —el diario
    que da el sesgo, el H4 que enfoca y el que bloquea— tienen que estar VIVOS
    en ese instante. Vivo es haber cerrado ya su vela de confirmación y no haber
    cerrado todavía la que lo resuelve.

    Se comprueba en los dos modos porque `strict` elige el rango que bloquea por
    otro camino que `latest`, y un modo podría adelantarse sin que el otro se
    enterase.
    """
    item = composed[0]
    states = item.states_by_mode[mode]
    assert not states.empty, "la fixture tiene que dar línea de estados"

    # Las ventanas se rehacen desde la REJILLA DE VELAS y no desde las columnas
    # que lleva el rango: si el punto de composición se equivocase al ponerlas,
    # comprobar la alineación contra ellas sería preguntarle al sospechoso.
    diario = _windows(run, item.frame, DAILY)
    cuatro = _windows(run, item.h4_frame, H4)
    ventanas = {
        "d1_range_id": diario,
        "h4_focus_range_id": cuatro,
        "h4_blocking_range_id": cuatro,
    }
    momentos = pd.DatetimeIndex(states.index)
    leidos = 0
    for column, ventana in ventanas.items():
        usados = states[column]
        for position, identifier in enumerate(usados):
            if pd.isna(identifier):
                continue
            leidos += 1
            existe, muere = ventana[int(identifier)]
            momento = momentos[position]
            assert existe <= momento, (
                f"{mode}: la fila {momento} usa el rango {identifier} de «{column}», "
                f"que no existía hasta {existe}"
            )
            assert pd.isna(muere) or momento < muere, (
                f"{mode}: la fila {momento} usa el rango {identifier} de «{column}», "
                f"que ya se había resuelto en {muere}"
            )
    assert leidos, "si no se usó ningún rango, el test no está comprobando nada"


def _windows(
    run: ChartRun, frame: pd.DataFrame, timeframe: str
) -> dict[int, tuple[pd.Timestamp, pd.Timestamp]]:
    """Desde cuándo existe cada rango y cuándo deja de existir, por número.

    Los dos instantes salen de la rejilla de velas —el cierre de la vela que lo
    confirmó y el de la que lo resolvió—, no de las columnas del rango.
    """
    labels = _labels(run)[timeframe]
    closes = pd.Series(bar_closes(labels, timeframe, run.config.aggregation), index=labels)
    existe = closes.reindex(pd.DatetimeIndex(frame["t_confirm"])).to_numpy()
    muere = closes.reindex(pd.DatetimeIndex(frame["t_resolved"])).to_numpy()
    return {
        int(identifier): (pd.Timestamp(existe[position]), pd.Timestamp(muere[position]))
        for position, identifier in enumerate(frame["range_id"].to_numpy(dtype=int))
    }
