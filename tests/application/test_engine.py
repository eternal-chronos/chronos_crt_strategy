"""El motor: orden de eventos, ausencia de lookahead y cuadre contable."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pandas as pd
import pytest

from chronos.application.backtest.config import BacktestConfig, RiskConfig
from chronos.application.backtest.engine import BacktestEngine
from chronos.domain.context import BarContext
from chronos.domain.enums import ExitReason, Side
from chronos.domain.errors import StrategyError
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.signal import EntrySignal, ExitSignal, StrategyAction
from chronos.domain.strategy import Strategy
from chronos.infrastructure.broker.simulated import build_simulated_broker
from tests.conftest import make_frame


class OpenOnBar(Strategy):
    """Abre una vez en la barra indicada. Mínimo necesario para probar el motor."""

    name = "test_open"

    def __init__(self, at_bar: int = 1, **params: object) -> None:
        super().__init__(at_bar=at_bar, **params)
        self.at_bar = at_bar
        self.closed: list[object] = []

    def on_bar(self, ctx: BarContext) -> Sequence[StrategyAction]:
        if ctx.index != self.at_bar:
            return ()
        return (
            EntrySignal(
                side=Side.BUY, volume=1.0, stop_distance=10.0, take_profit_distance=20.0
            ),
        )

    def on_trade_closed(self, trade: object) -> None:
        self.closed.append(trade)


class PeekIntoTheFuture(Strategy):
    """Intenta leer una barra futura. Debe fallar, no devolver datos."""

    name = "test_peek"

    def on_bar(self, ctx: BarContext) -> Sequence[StrategyAction]:
        ctx.value("close", offset=-1)
        return ()


def _data(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return make_frame(rows)


def _engine(spec: InstrumentSpec, config: BacktestConfig) -> BacktestEngine:
    """El motor con su bróker simulado: el punto de composición de los tests."""
    return BacktestEngine(spec, config, build_simulated_broker(spec, config))


def _config(config: BacktestConfig, **risk: object) -> BacktestConfig:
    return replace(config, risk=replace(config.risk, **risk))  # type: ignore[arg-type]


# --- Orden de eventos -------------------------------------------------------


def test_la_señal_se_ejecuta_en_la_apertura_de_la_barra_siguiente(
    no_cost_spec: InstrumentSpec, config: BacktestConfig
) -> None:
    rows = [
        (2000.0, 2001.0, 1999.0, 2000.0),
        (2000.0, 2001.0, 1999.0, 2000.0),  # barra 1: la estrategia decide
        (2003.0, 2004.0, 2002.0, 2003.0),  # barra 2: se ejecuta en su apertura
        (2003.0, 2004.0, 2002.0, 2003.0),
    ]
    engine = _engine(no_cost_spec, _config(config, max_concurrent_positions=1))
    result = engine.run(_data(rows), OpenOnBar(at_bar=1))

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(2003.0)  # apertura de la barra 2
    assert trade.entry_index == 2


def test_el_calentamiento_bloquea_las_primeras_barras(
    no_cost_spec: InstrumentSpec, config: BacktestConfig
) -> None:
    class LateStarter(OpenOnBar):
        @property
        def warmup_bars(self) -> int:
            return 3

    rows = [(2000.0, 2001.0, 1999.0, 2000.0)] * 6
    engine = _engine(no_cost_spec, config)
    result = engine.run(_data(rows), LateStarter(at_bar=1))
    assert not result.trades  # la barra 1 cae dentro del calentamiento


def test_leer_una_barra_futura_es_un_error(
    no_cost_spec: InstrumentSpec, config: BacktestConfig
) -> None:
    rows = [(2000.0, 2001.0, 1999.0, 2000.0)] * 5
    engine = _engine(no_cost_spec, config)
    with pytest.raises(StrategyError):
        engine.run(_data(rows), PeekIntoTheFuture())


def test_lo_que_queda_abierto_se_liquida_al_final(
    no_cost_spec: InstrumentSpec, config: BacktestConfig
) -> None:
    rows = [(2000.0, 2000.5, 1999.5, 2000.0)] * 5
    engine = _engine(no_cost_spec, config)
    result = engine.run(_data(rows), OpenOnBar(at_bar=1))
    assert len(result.trades) == 1
    assert result.trades[0].reason is ExitReason.END_OF_DATA


# --- Cuadre contable --------------------------------------------------------


def test_el_balance_final_es_la_suma_de_los_resultados_netos(
    spec: InstrumentSpec, config: BacktestConfig
) -> None:
    """Comisión y swap se cargan al abrir/devengar, pero no deben contarse dos veces."""
    rows = [
        (2000.0, 2001.0, 1999.0, 2000.0),
        (2000.0, 2001.0, 1999.0, 2000.0),
        (2000.0, 2025.0, 1999.0, 2020.0),  # toca el objetivo (+20)
        (2020.0, 2021.0, 2019.0, 2020.0),
    ]
    engine = _engine(spec, config)
    result = engine.run(_data(rows), OpenOnBar(at_bar=1))

    esperado = result.initial_balance + sum(t.net_pnl for t in result.trades)
    assert result.final_balance == pytest.approx(esperado)
    assert result.final_equity == pytest.approx(esperado)  # sin posiciones abiertas


def test_el_equity_no_cuenta_dos_veces_la_comision(
    spec: InstrumentSpec, config: BacktestConfig
) -> None:
    rows = [(2000.0, 2000.5, 1999.5, 2000.0)] * 6
    engine = _engine(spec, config)
    result = engine.run(_data(rows), OpenOnBar(at_bar=1))

    # Con la posición abierta (barra 3), el equity es balance + P&L bruto flotante.
    curva = result.equity_curve
    abiertas = curva[curva["exposure"] > 0]
    assert not abiertas.empty
    diferencia = (abiertas["equity"] - abiertas["balance"]).abs().max()
    assert diferencia < 100.0  # solo la horquilla, no una comisión duplicada


# --- Límites de riesgo ------------------------------------------------------


def test_no_se_supera_el_maximo_de_posiciones_simultaneas(
    no_cost_spec: InstrumentSpec, config: BacktestConfig
) -> None:
    class AlwaysOpen(Strategy):
        name = "always"

        def on_bar(self, ctx: BarContext) -> Sequence[StrategyAction]:
            return (EntrySignal(side=Side.BUY, volume=0.1, stop_distance=50.0),)

    rows = [(2000.0, 2000.5, 1999.5, 2000.0)] * 10
    engine = _engine(no_cost_spec, _config(config, max_concurrent_positions=2))
    result = engine.run(_data(rows), AlwaysOpen())

    assert int(result.equity_curve["exposure"].max()) == 2
    assert result.rejections.get("max_positions", 0) > 0


def test_el_drawdown_maximo_detiene_la_corrida(
    no_cost_spec: InstrumentSpec, config: BacktestConfig
) -> None:
    rows = [
        (2000.0, 2001.0, 1999.0, 2000.0),
        (2000.0, 2001.0, 1999.0, 2000.0),
        # Caída de 15 USD con 1 lote = -1500 USD = 15% de drawdown, suficiente
        # para el límite de riesgo pero lejos del stop out por margen.
        (2000.0, 2001.0, 1984.0, 1985.0),
        (1985.0, 1986.0, 1984.0, 1985.0),
    ]
    risk = RiskConfig(
        sizing="fixed_lot", fixed_lot=1.0, max_daily_loss=None, max_drawdown_stop=0.10
    )
    engine = _engine(no_cost_spec, replace(config, risk=risk))

    class NoStop(OpenOnBar):
        def on_bar(self, ctx: BarContext) -> Sequence[StrategyAction]:
            if ctx.index != self.at_bar:
                return ()
            return (EntrySignal(side=Side.BUY, volume=1.0),)  # sin stop loss

    result = engine.run(_data(rows), NoStop(at_bar=1))
    assert result.halted_reason is not None
    assert "Drawdown" in result.halted_reason
    assert result.trades[-1].reason is ExitReason.RISK_LIMIT


def test_una_señal_de_cierre_cierra_la_posicion(
    no_cost_spec: InstrumentSpec, config: BacktestConfig
) -> None:
    class OpenThenClose(Strategy):
        name = "open_close"

        def on_bar(self, ctx: BarContext) -> Sequence[StrategyAction]:
            if ctx.index == 1:
                return (EntrySignal(side=Side.BUY, volume=1.0, stop_distance=50.0),)
            if ctx.index == 3:
                return (ExitSignal(reason=ExitReason.SIGNAL),)
            return ()

    rows = [(2000.0, 2001.0, 1999.0, 2000.0)] * 6
    engine = _engine(no_cost_spec, config)
    result = engine.run(_data(rows), OpenThenClose())

    assert len(result.trades) == 1
    assert result.trades[0].reason is ExitReason.SIGNAL
    assert result.trades[0].exit_index == 4  # se ejecuta en la barra siguiente
