"""Fixtures compartidas."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from chronos.application.backtest.config import (
    AccountConfig,
    BacktestConfig,
    DataConfig,
    ExecutionConfig,
    RiskConfig,
)
from chronos.domain.account import Account
from chronos.domain.enums import Timeframe
from chronos.domain.instrument import CostModel, InstrumentSpec, SessionSpec, SwapModel


@pytest.fixture
def spec() -> InstrumentSpec:
    """XAUUSD con costes sencillos y redondos, para que las cuentas sean verificables."""
    return InstrumentSpec(
        symbol="XAUUSD",
        digits=2,
        tick_size=0.01,
        pip_size=0.01,
        contract_size=100.0,
        min_lot=0.01,
        max_lot=100.0,
        lot_step=0.01,
        leverage=200.0,
        costs=CostModel(
            spread_mode="fixed",
            spread_points=20.0,  # 0.20 USD
            commission_per_lot_per_side=3.0,
            slippage_points=0.0,
        ),
        swap=SwapModel(long_points=-8.0, short_points=3.0, triple_swap_weekday=2),
        session=SessionSpec(timezone="UTC", break_start=None, break_end=None),
    )


@pytest.fixture
def no_cost_spec(spec: InstrumentSpec) -> InstrumentSpec:
    """Mismo símbolo sin fricción: aísla la lógica del motor de los costes."""
    return InstrumentSpec(
        symbol=spec.symbol,
        digits=spec.digits,
        tick_size=spec.tick_size,
        pip_size=spec.pip_size,
        contract_size=spec.contract_size,
        min_lot=spec.min_lot,
        max_lot=spec.max_lot,
        lot_step=spec.lot_step,
        leverage=spec.leverage,
        costs=CostModel(spread_points=0.0, commission_per_lot_per_side=0.0, slippage_points=0.0),
        swap=SwapModel(long_points=0.0, short_points=0.0),
        session=spec.session,
    )


@pytest.fixture
def account() -> Account:
    return Account(initial_balance=10_000.0, leverage=200.0)


@pytest.fixture
def execution() -> ExecutionConfig:
    return ExecutionConfig(fill_model="next_bar_open", intrabar_priority="worst", price_basis="bid")


@pytest.fixture
def config() -> BacktestConfig:
    return BacktestConfig(
        account=AccountConfig(initial_balance=10_000.0),
        execution=ExecutionConfig(),
        risk=RiskConfig(sizing="fixed_lot", fixed_lot=0.10, max_daily_loss=None, max_drawdown_stop=None),
        data=DataConfig(timeframe=Timeframe.M15, strategy_timeframe=Timeframe.M15),
        strategy_name="test",
    )


@pytest.fixture
def moment() -> datetime:
    return datetime(2024, 3, 4, 10, 0, tzinfo=UTC)


def make_frame(rows: list[tuple[float, float, float, float]], start: str = "2024-03-04 00:00") -> pd.DataFrame:
    """DataFrame canónico a partir de tuplas (open, high, low, close)."""
    index = pd.date_range(start=start, periods=len(rows), freq="15min", tz="UTC")
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    frame["volume"] = 100.0
    frame.index.name = "timestamp"
    return frame


#: Semana del oro en UTC: abre el domingo a las 22:00 y cierra el viernes a las 21:00.
WEEK_OPEN = (6, 22)
WEEK_CLOSE = (4, 21)
#: Minuto del día en el que se concentran los datos macro de EE. UU.
VOLATILITY_PEAK_MINUTE = 13 * 60 + 30


def make_m1_history(
    weeks: int = 8,
    start: str = "2024-01-07 22:00",
    seed: int = 7,
    step: float = 0.02,
    shift_hours: float = 0.0,
) -> pd.DataFrame:
    """Histórico M1 sintético con la microestructura que audita §1.1.

    No pretende parecerse al oro: sólo reproduce los dos hechos que la
    verificación de zona horaria comprueba —el hueco de fin de semana y el pico
    de volatilidad de las 13:30 UTC— para poder probar que la auditoría los
    detecta y que se rompe cuando el histórico viene desplazado.
    """
    rng = np.random.default_rng(seed)
    origin = pd.Timestamp(start, tz="UTC")
    minutes = pd.date_range(origin, periods=weeks * 7 * 24 * 60, freq="1min", tz="UTC")

    weekday = minutes.weekday.to_numpy()
    minute_of_day = (minutes.hour * 60 + minutes.minute).to_numpy()
    open_at = (weekday == WEEK_OPEN[0]) & (minute_of_day >= WEEK_OPEN[1] * 60)
    close_at = (weekday == WEEK_CLOSE[0]) & (minute_of_day >= WEEK_CLOSE[1] * 60)
    tradeable = ((weekday <= WEEK_CLOSE[0]) & ~close_at) | open_at
    minutes = minutes[tradeable]
    minute_of_day = (minutes.hour * 60 + minutes.minute).to_numpy()

    close = 2000.0 + np.cumsum(rng.normal(0.0, step, size=len(minutes)))
    open_ = np.empty_like(close)
    open_[0] = 2000.0
    open_[1:] = close[:-1]

    # Media campana centrada en el pico: el rango por minuto se multiplica hasta
    # por seis en torno a las 13:30 UTC.
    distance = np.minimum(
        np.abs(minute_of_day - VOLATILITY_PEAK_MINUTE),
        1440 - np.abs(minute_of_day - VOLATILITY_PEAK_MINUTE),
    )
    half_range = 0.05 * (1.0 + 5.0 * np.exp(-((distance / 20.0) ** 2)))

    frame = pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + half_range,
            "low": np.minimum(open_, close) - half_range,
            "close": close,
            "volume": 100.0,
        },
        index=minutes,
    )
    frame.index.name = "timestamp"
    if shift_hours:
        frame.index = frame.index + pd.Timedelta(hours=shift_hours)
    return frame
