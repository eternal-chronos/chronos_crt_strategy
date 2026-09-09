"""Métricas de rendimiento a partir de la curva de equity y las operaciones.

Las métricas de riesgo/retorno se calculan sobre retornos DIARIOS del equity, no
sobre retornos por barra: en intradía la mayoría de barras no tienen posición
abierta y diluirían artificialmente la volatilidad (y con ella el Sharpe).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

from chronos.application.backtest.result import BacktestResult
from chronos.domain.trade import Trade

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Resumen cuantitativo de una corrida."""

    # Resultado
    initial_balance: float
    final_balance: float
    net_profit: float
    return_pct: float
    cagr: float

    # Riesgo
    max_drawdown: float
    max_drawdown_pct: float
    max_drawdown_duration_days: float
    volatility_annual: float
    sharpe: float
    sortino: float
    calmar: float
    ulcer_index: float
    recovery_factor: float

    # Operativa
    total_trades: int
    winners: int
    losers: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    expectancy: float
    expectancy_r: float | None
    avg_win: float
    avg_loss: float
    payoff_ratio: float
    largest_win: float
    largest_loss: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_bars_held: float
    exposure_pct: float

    # Costes
    total_commission: float
    total_swap: float

    # Contexto
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None
    days: float = 0.0
    halted_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = str(self.start) if self.start is not None else None
        data["end"] = str(self.end) if self.end is not None else None
        return data


def compute_performance(result: BacktestResult) -> PerformanceReport:
    """Calcula todas las métricas de una corrida."""
    curve = result.equity_curve
    equity = curve["equity"] if not curve.empty else pd.Series(dtype=float)
    trades = result.trades

    initial = result.initial_balance
    final = float(equity.iloc[-1]) if len(equity) else initial
    net_profit = final - initial
    return_pct = net_profit / initial if initial else 0.0

    index = pd.DatetimeIndex(curve.index) if len(curve) else None
    start = pd.Timestamp(index[0]) if index is not None else None
    end = pd.Timestamp(index[-1]) if index is not None else None
    days = (end - start).total_seconds() / 86_400 if start is not None and end is not None else 0.0
    years = days / 365.25 if days > 0 else 0.0

    cagr = _cagr(initial, final, years)
    dd_abs, dd_pct, dd_days = _drawdown_stats(equity)
    daily = _daily_returns(equity)
    volatility = float(daily.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(daily) > 1 else 0.0

    return PerformanceReport(
        initial_balance=initial,
        final_balance=final,
        net_profit=net_profit,
        return_pct=return_pct,
        cagr=cagr,
        max_drawdown=dd_abs,
        max_drawdown_pct=dd_pct,
        max_drawdown_duration_days=dd_days,
        volatility_annual=volatility,
        sharpe=_sharpe(daily),
        sortino=_sortino(daily),
        calmar=(cagr / dd_pct) if dd_pct > 0 else 0.0,
        ulcer_index=_ulcer_index(equity),
        recovery_factor=(net_profit / dd_abs) if dd_abs > 0 else 0.0,
        exposure_pct=_exposure(curve),
        halted_reason=result.halted_reason,
        start=start,
        end=end,
        days=days,
        **_trade_stats(trades),
    )


# --- Bloques de cálculo -----------------------------------------------------


def _cagr(initial: float, final: float, years: float) -> float:
    if years <= 0 or initial <= 0 or final <= 0:
        return 0.0
    return float((final / initial) ** (1.0 / years) - 1.0)


def _daily_returns(equity: pd.Series) -> pd.Series:
    if len(equity) < 2:
        return pd.Series(dtype=float)
    daily = equity.resample("1D").last().dropna()
    return daily.pct_change().dropna()


def _sharpe(daily_returns: pd.Series, risk_free: float = 0.0) -> float:
    """Sharpe anualizado. Tasa libre de riesgo en términos anuales."""
    if len(daily_returns) < 2:
        return 0.0
    std = float(daily_returns.std(ddof=1))
    if std == 0:
        return 0.0
    excess = float(daily_returns.mean()) - risk_free / TRADING_DAYS_PER_YEAR
    return float(excess / std * np.sqrt(TRADING_DAYS_PER_YEAR))


def _sortino(daily_returns: pd.Series, target: float = 0.0) -> float:
    if len(daily_returns) < 2:
        return 0.0
    downside = daily_returns[daily_returns < target]
    if downside.empty:
        return 0.0
    downside_dev = float(np.sqrt((downside**2).mean()))
    if downside_dev == 0:
        return 0.0
    annualized = (float(daily_returns.mean()) - target) / downside_dev
    return float(annualized * np.sqrt(TRADING_DAYS_PER_YEAR))


def _drawdown_stats(equity: pd.Series) -> tuple[float, float, float]:
    """Devuelve (drawdown absoluto máximo, en %, duración máxima en días)."""
    if equity.empty:
        return 0.0, 0.0, 0.0
    peak = equity.cummax()
    drawdown = equity - peak
    dd_abs = float(-drawdown.min())
    dd_pct = float((-(drawdown / peak)).max()) if (peak > 0).all() else 0.0

    # Duración: tramo más largo sin superar el máximo anterior.
    in_dd = equity < peak
    longest = pd.Timedelta(0)
    current_start: pd.Timestamp | None = None
    for timestamp, flag in in_dd.items():
        if flag and current_start is None:
            current_start = pd.Timestamp(cast(Any, timestamp))
        elif not flag and current_start is not None:
            longest = max(longest, pd.Timestamp(cast(Any, timestamp)) - current_start)
            current_start = None
    if current_start is not None:
        longest = max(longest, pd.Timestamp(cast(Any, equity.index[-1])) - current_start)
    return dd_abs, dd_pct, longest.total_seconds() / 86_400


def _ulcer_index(equity: pd.Series) -> float:
    """Raíz del promedio de los drawdowns al cuadrado: penaliza caídas profundas y largas."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    drawdown_pct = (equity - peak) / peak * 100.0
    return float(np.sqrt((drawdown_pct**2).mean()))


def _exposure(curve: pd.DataFrame) -> float:
    """Porcentaje de barras con al menos una posición abierta."""
    if curve.empty or "exposure" not in curve:
        return 0.0
    return float((curve["exposure"] > 0).mean())


def _trade_stats(trades: Sequence[Trade]) -> dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "winners": 0, "losers": 0, "win_rate": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "profit_factor": 0.0,
            "expectancy": 0.0, "expectancy_r": None, "avg_win": 0.0, "avg_loss": 0.0,
            "payoff_ratio": 0.0, "largest_win": 0.0, "largest_loss": 0.0,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            "avg_bars_held": 0.0, "total_commission": 0.0, "total_swap": 0.0,
        }

    pnl = np.array([t.net_pnl for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]

    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(-losses.mean()) if losses.size else 0.0

    return {
        "total_trades": len(trades),
        "winners": int(wins.size),
        "losers": int(losses.size),
        "win_rate": float(wins.size / len(trades)),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": float(pnl.mean()),
        "expectancy_r": float(np.mean(r_values)) if r_values else None,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / avg_loss) if avg_loss > 0 else 0.0,
        "largest_win": float(pnl.max()),
        "largest_loss": float(pnl.min()),
        "max_consecutive_wins": _max_streak(pnl > 0),
        "max_consecutive_losses": _max_streak(pnl <= 0),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
        "total_commission": float(sum(t.commission for t in trades)),
        "total_swap": float(sum(t.swap for t in trades)),
    }


def _max_streak(flags: np.ndarray) -> int:
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best
