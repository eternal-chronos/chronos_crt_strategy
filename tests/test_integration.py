"""Prueba de punta a punta: datos → motor → métricas → informe."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chronos.application.backtest.config import (
    AccountConfig,
    BacktestConfig,
    DataConfig,
    ExecutionConfig,
    ReportingConfig,
    RiskConfig,
)
from chronos.application.run_backtest import BacktestRun, run_backtest
from chronos.domain.enums import Timeframe
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.strategies.registry import available_strategies, create_strategy
from chronos.domain.strategy import Strategy
from chronos.infrastructure.broker.simulated import build_simulated_broker
from chronos.infrastructure.data.market_data import SyntheticMarketData
from chronos.infrastructure.reporting.report import ReportWriter


@pytest.fixture
def run_config(tmp_path: Path) -> BacktestConfig:
    return BacktestConfig(
        account=AccountConfig(initial_balance=10_000.0),
        execution=ExecutionConfig(),
        risk=RiskConfig(sizing="fixed_fractional", risk_per_trade=0.005),
        data=DataConfig(
            source="synthetic", timeframe=Timeframe.M1, strategy_timeframe=Timeframe.M15
        ),
        reporting=ReportingConfig(output_dir=str(tmp_path)),
        strategy_name="ema_cross",
        strategy_params={"fast_period": 10, "slow_period": 30, "atr_period": 14},
    )


def _run(
    spec: InstrumentSpec,
    config: BacktestConfig,
    market_data: SyntheticMarketData,
    strategy: Strategy,
) -> BacktestRun:
    """Punto de composición de los tests: puertos a mano, como en la CLI."""
    return run_backtest(
        spec=spec,
        config=config,
        market_data=market_data,
        broker=build_simulated_broker(spec, config),
        strategy=strategy,
    )


def test_la_estrategia_de_referencia_esta_registrada() -> None:
    assert "ema_cross" in available_strategies()


def test_corrida_completa_sobre_datos_sinteticos(
    spec: InstrumentSpec, run_config: BacktestConfig
) -> None:
    market_data = SyntheticMarketData(symbol=spec.symbol, periods=30_000, seed=3)
    strategy = create_strategy("ema_cross", dict(run_config.strategy_params))
    run = _run(spec, run_config, market_data, strategy)

    assert run.result.bars_processed > 0
    assert run.result.trades, "la estrategia de referencia debería operar en 30 000 barras"
    # El cuadre contable es la comprobación que no puede fallar nunca.
    esperado = run.result.initial_balance + sum(t.net_pnl for t in run.result.trades)
    assert run.result.final_balance == pytest.approx(esperado, abs=1e-6)


def test_los_costes_se_aplican_de_verdad(spec: InstrumentSpec, run_config: BacktestConfig) -> None:
    market_data = SyntheticMarketData(symbol=spec.symbol, periods=30_000, seed=3)
    strategy = create_strategy("ema_cross", dict(run_config.strategy_params))
    run = _run(spec, run_config, market_data, strategy)

    assert run.performance.total_commission < 0, "no se cobró ninguna comisión"
    assert run.performance.total_swap != 0, "no se devengó swap en ninguna posición"


def test_desactivar_los_costes_mejora_el_resultado(
    spec: InstrumentSpec, no_cost_spec: InstrumentSpec, run_config: BacktestConfig
) -> None:
    """Verificación de sensibilidad: la fricción tiene que doler."""
    params = dict(run_config.strategy_params)
    market_data = SyntheticMarketData(symbol=spec.symbol, periods=30_000, seed=3)

    con_costes = _run(spec, run_config, market_data, create_strategy("ema_cross", params))
    sin_costes = _run(no_cost_spec, run_config, market_data, create_strategy("ema_cross", params))

    assert sin_costes.performance.net_profit > con_costes.performance.net_profit


def test_el_informe_se_escribe_completo(
    spec: InstrumentSpec, run_config: BacktestConfig, tmp_path: Path
) -> None:
    market_data = SyntheticMarketData(symbol=spec.symbol, periods=20_000, seed=5)
    strategy = create_strategy("ema_cross", dict(run_config.strategy_params))
    run = _run(spec, run_config, market_data, strategy)

    folder = ReportWriter(tmp_path).write(run, prices=market_data.frame, run_id="test")

    for name in ("report.html", "trades.csv", "equity.csv", "metrics.json", "run.json"):
        assert (folder / name).is_file(), f"falta {name}"

    html = (folder / "report.html").read_text(encoding="utf-8")
    assert "Curva de capital" in html
    assert "Plotly" in html  # la librería va embebida: el informe funciona sin red


def test_el_timeframe_de_estrategia_no_puede_ser_mas_fino_que_los_datos(
    spec: InstrumentSpec, run_config: BacktestConfig
) -> None:
    from chronos.domain.errors import DomainError

    market_data = SyntheticMarketData(
        symbol=spec.symbol, base_timeframe=Timeframe.H1, periods=2_000
    )
    config = replace(run_config, data=replace(run_config.data, strategy_timeframe=Timeframe.M15))
    strategy = create_strategy("ema_cross", dict(config.strategy_params))

    with pytest.raises(DomainError):
        _run(spec, config, market_data, strategy)
