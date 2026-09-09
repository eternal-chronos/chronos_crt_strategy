"""Dimensionamiento de posición y métricas de rendimiento."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from chronos.application.backtest.config import RiskConfig
from chronos.application.backtest.result import BacktestResult
from chronos.application.metrics.performance import compute_performance
from chronos.application.risk.sizing import build_sizer
from chronos.domain.enums import ExitReason, Side, Timeframe
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.trade import Trade

# --- Sizing -----------------------------------------------------------------


def test_fraccion_fija_arriesga_el_porcentaje_pedido(spec: InstrumentSpec) -> None:
    sizer = build_sizer(RiskConfig(sizing="fixed_fractional", risk_per_trade=0.01, max_lot=10.0))
    lots = sizer.size(spec=spec, equity=10_000.0, entry_price=2000.0, stop_loss=1990.0)

    # 1% de 10 000 = 100 USD; stop de 10 USD -> 100 / (10 * 100 oz) = 0.10 lotes
    assert lots == pytest.approx(0.10)
    assert spec.gross_pnl(Side.BUY, 2000.0, 1990.0, lots) == pytest.approx(-100.0)


def test_sin_stop_la_fraccion_fija_no_opera(spec: InstrumentSpec) -> None:
    sizer = build_sizer(RiskConfig(sizing="fixed_fractional"))
    assert sizer.size(spec=spec, equity=10_000.0, entry_price=2000.0, stop_loss=None) == 0.0


def test_el_lotaje_se_limita_al_maximo(spec: InstrumentSpec) -> None:
    sizer = build_sizer(RiskConfig(sizing="fixed_fractional", risk_per_trade=0.5, max_lot=1.0))
    lots = sizer.size(spec=spec, equity=100_000.0, entry_price=2000.0, stop_loss=1999.0)
    assert lots == pytest.approx(1.0)


def test_lote_fijo_ignora_el_equity(spec: InstrumentSpec) -> None:
    sizer = build_sizer(RiskConfig(sizing="fixed_lot", fixed_lot=0.25, max_lot=5.0))
    assert sizer.size(spec=spec, equity=1_000.0, entry_price=2000.0, stop_loss=None) == pytest.approx(0.25)


def test_riesgo_en_efectivo_fijo(spec: InstrumentSpec) -> None:
    sizer = build_sizer(RiskConfig(sizing="fixed_cash_risk", fixed_cash_risk=200.0, max_lot=10.0))
    lots = sizer.size(spec=spec, equity=10_000.0, entry_price=2000.0, stop_loss=1990.0)
    assert lots == pytest.approx(0.20)


# --- Métricas ---------------------------------------------------------------


def _trade(pnl: float, index: int, risk: float | None = 100.0) -> Trade:
    entry = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index)
    return Trade(
        id=index,
        symbol="XAUUSD",
        side=Side.BUY,
        volume=0.1,
        entry_price=2000.0,
        entry_time=entry,
        entry_index=index * 10,
        exit_price=2000.0 + pnl,
        exit_time=entry + timedelta(hours=4),
        exit_index=index * 10 + 5,
        reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
        gross_pnl=pnl + 6.0,
        commission=-6.0,
        swap=0.0,
        net_pnl=pnl,
        risk_amount=risk,
    )


def _result(trades: list[Trade], equity: list[float]) -> BacktestResult:
    index = pd.date_range("2024-01-01", periods=len(equity), freq="1D", tz="UTC")
    curve = pd.DataFrame(
        {"balance": equity, "equity": equity, "exposure": [1] * len(equity)}, index=index
    )
    return BacktestResult(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        initial_balance=equity[0],
        trades=trades,
        equity_curve=curve,
        strategy={"name": "test", "params": {}},
    )


def test_metricas_basicas_de_operativa() -> None:
    trades = [_trade(200.0, 1), _trade(-100.0, 2), _trade(300.0, 3), _trade(-100.0, 4)]
    report = compute_performance(_result(trades, [10_000, 10_200, 10_100, 10_400, 10_300]))

    assert report.total_trades == 4
    assert report.winners == 2
    assert report.win_rate == pytest.approx(0.5)
    assert report.gross_profit == pytest.approx(500.0)
    assert report.gross_loss == pytest.approx(200.0)
    assert report.profit_factor == pytest.approx(2.5)
    assert report.expectancy == pytest.approx(75.0)
    assert report.expectancy_r == pytest.approx(0.75)  # riesgo de 100 por operación
    assert report.total_commission == pytest.approx(-24.0)


def test_drawdown_maximo_sobre_la_curva() -> None:
    equity = [10_000, 11_000, 9_900, 10_500]
    report = compute_performance(_result([], equity))

    assert report.max_drawdown == pytest.approx(1_100.0)
    assert report.max_drawdown_pct == pytest.approx(0.10)


def test_rachas_consecutivas() -> None:
    trades = [_trade(100.0, i) for i in range(1, 4)] + [_trade(-50.0, i) for i in range(4, 6)]
    report = compute_performance(_result(trades, [10_000] * 6))

    assert report.max_consecutive_wins == 3
    assert report.max_consecutive_losses == 2


def test_una_corrida_sin_operaciones_no_rompe_las_metricas() -> None:
    report = compute_performance(_result([], [10_000, 10_000, 10_000]))

    assert report.total_trades == 0
    assert report.profit_factor == 0.0
    assert report.net_profit == pytest.approx(0.0)
    assert report.expectancy_r is None
