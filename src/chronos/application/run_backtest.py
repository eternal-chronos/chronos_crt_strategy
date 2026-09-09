"""Orquestación de una corrida: pedir barras, simular, evaluar.

Es una función, no una clase: recibe los puertos ya construidos (`MarketData`,
`Broker`) y la estrategia. Quién los construye —CLI, notebook, tests— es
problema de la capa externa.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from chronos.application.backtest.config import BacktestConfig
from chronos.application.backtest.engine import BacktestEngine
from chronos.application.backtest.result import BacktestResult
from chronos.application.metrics.performance import PerformanceReport, compute_performance
from chronos.application.ports import Broker, MarketData
from chronos.domain.bars import validate_bars
from chronos.domain.errors import DomainError
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.strategy import Strategy


@dataclass(frozen=True, slots=True)
class BacktestRun:
    """Corrida completa: qué pasó y cómo de bien lo hizo."""

    result: BacktestResult
    performance: PerformanceReport
    config: BacktestConfig
    spec: InstrumentSpec


def run_backtest(
    *,
    spec: InstrumentSpec,
    config: BacktestConfig,
    market_data: MarketData,
    broker: Broker,
    strategy: Strategy,
) -> BacktestRun:
    """Ejecuta `strategy` sobre el histórico de `spec.symbol` y la evalúa."""
    bars = load_bars(spec=spec, config=config, market_data=market_data)
    if len(bars) <= strategy.warmup_bars:
        raise DomainError(
            f"Solo hay {len(bars)} barras y la estrategia necesita "
            f"{strategy.warmup_bars} de calentamiento"
        )

    result = BacktestEngine(spec, config, broker).run(bars, strategy)
    return BacktestRun(
        result=result,
        performance=compute_performance(result),
        config=config,
        spec=spec,
    )


def load_bars(
    *, spec: InstrumentSpec, config: BacktestConfig, market_data: MarketData
) -> pd.DataFrame:
    """Barras de la corrida, validadas en la frontera de `application/`."""
    data = config.data
    bars = market_data.bars(
        spec.symbol,
        data.strategy_timeframe,
        since=data.start,
        until=data.end,
    )
    validate_bars(bars)
    return bars
