"""Los tres puertos: que el adaptador cumpla el contrato y recorte donde debe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from chronos.application.backtest.config import BacktestConfig
from chronos.application.ports import Broker, Clock, MarketData
from chronos.domain.bars import validate_bars
from chronos.domain.enums import Timeframe
from chronos.domain.instrument import InstrumentSpec
from chronos.infrastructure.broker.simulated import build_simulated_broker
from chronos.infrastructure.clock import FixedClock, SystemClock
from chronos.infrastructure.data.market_data import FrameMarketData, SyntheticMarketData

# --- Conformidad ------------------------------------------------------------


def test_los_adaptadores_cumplen_su_puerto(spec: InstrumentSpec, config: BacktestConfig) -> None:
    """Comprobación estructural: si un adaptador se desalinea, esto deja de asignar."""
    clock: Clock = SystemClock()
    simulated_clock: Clock = FixedClock(datetime(2024, 3, 4, tzinfo=UTC))
    market_data: MarketData = SyntheticMarketData(periods=100)
    broker: Broker = build_simulated_broker(spec, config)

    assert clock.now().tzinfo is not None
    assert simulated_clock.now() == datetime(2024, 3, 4, tzinfo=UTC)
    assert not broker.positions
    assert not market_data.bars("XAUUSD", Timeframe.M1).empty


# --- MarketData -------------------------------------------------------------


@pytest.fixture
def market_data() -> FrameMarketData:
    return SyntheticMarketData(symbol="XAUUSD", base_timeframe=Timeframe.M1, periods=5_000)


def test_bars_recorta_en_el_adaptador(market_data: FrameMarketData) -> None:
    completo = market_data.bars("XAUUSD", Timeframe.M1)
    corte = completo.index[1_000]

    recortado = market_data.bars("XAUUSD", Timeframe.M1, until=corte)

    assert recortado.index[-1] == corte
    assert len(recortado) == 1_001  # `until` está incluido: la barra de `t` cerró en `t`
    validate_bars(recortado)


def test_no_hay_lookahead_al_resamplear_con_until(market_data: FrameMarketData) -> None:
    """Lo que se entrega hasta `until` es el prefijo exacto del histórico completo.

    Dos trampas a la vez: resamplear antes de recortar formaría la vela con
    barras posteriores a `until`, y entregar la vela a medio formar daría a la
    estrategia una vela que en ese instante todavía no ha cerrado.
    """
    completo = market_data.bars("XAUUSD", Timeframe.M15)
    corte = market_data.bars("XAUUSD", Timeframe.M1).index[1_007]  # mitad de una M15

    truncado = market_data.bars("XAUUSD", Timeframe.M15, until=corte)

    ultima_cerrada = truncado.index[-1] + pd.Timedelta(minutes=Timeframe.M15.minutes)
    assert ultima_cerrada <= corte + pd.Timedelta(minutes=Timeframe.M1.minutes)
    pd.testing.assert_frame_equal(truncado, completo.iloc[: len(truncado)])


def test_la_vela_agregada_a_medio_formar_no_se_entrega(market_data: FrameMarketData) -> None:
    """5 000 barras M1 no son múltiplo de 15: la última M15 está incompleta."""
    m1 = market_data.bars("XAUUSD", Timeframe.M1)
    m15 = market_data.bars("XAUUSD", Timeframe.M15)

    assert len(m1) == 5_000
    assert len(m15) == 5_000 // 15  # la vela 334, a medio formar, se queda fuera


def test_bars_respeta_since(market_data: FrameMarketData) -> None:
    completo = market_data.bars("XAUUSD", Timeframe.M1)
    desde = completo.index[500]

    recortado = market_data.bars("XAUUSD", Timeframe.M1, since=desde)

    assert recortado.index[0] == desde
    assert len(recortado) == len(completo) - 500


# --- Clock ------------------------------------------------------------------


def test_el_reloj_simulado_avanza_a_mano() -> None:
    clock = FixedClock(datetime(2024, 3, 4, 10, 0, tzinfo=UTC))

    assert clock.now() == datetime(2024, 3, 4, 10, 0, tzinfo=UTC)
    assert clock.advance(timedelta(hours=2)) == datetime(2024, 3, 4, 12, 0, tzinfo=UTC)
    assert clock.now() == datetime(2024, 3, 4, 12, 0, tzinfo=UTC)


def test_un_reloj_naif_se_interpreta_en_utc() -> None:
    assert FixedClock(datetime(2024, 3, 4, 10, 0)).now() == datetime(2024, 3, 4, 10, 0, tzinfo=UTC)
