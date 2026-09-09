"""Invariantes de la posición y contabilidad de la cuenta."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chronos.domain.account import Account
from chronos.domain.enums import ExitReason, Side
from chronos.domain.errors import (
    DomainError,
    InsufficientMargin,
    InvalidOrder,
    PositionAlreadyClosed,
)
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.position import Position
from chronos.domain.signal import EntrySignal


def _long(spec: InstrumentSpec, **overrides: object) -> Position:
    defaults = {
        "id": 1,
        "symbol": spec.symbol,
        "side": Side.BUY,
        "volume": 1.0,
        "entry_price": 2000.0,
        "entry_time": datetime(2024, 3, 4, 10, 0, tzinfo=UTC),
        "entry_index": 10,
        "stop_loss": 1995.0,
        "take_profit": 2010.0,
    }
    defaults.update(overrides)
    return Position(**defaults)  # type: ignore[arg-type]


# --- Invariantes ------------------------------------------------------------


def test_stop_de_un_largo_por_encima_de_la_entrada_falla(spec: InstrumentSpec) -> None:
    with pytest.raises(InvalidOrder):
        _long(spec, stop_loss=2005.0)


def test_objetivo_de_un_corto_por_encima_de_la_entrada_falla(spec: InstrumentSpec) -> None:
    with pytest.raises(InvalidOrder):
        _long(spec, side=Side.SELL, stop_loss=2005.0, take_profit=2010.0)


def test_volumen_no_positivo_falla(spec: InstrumentSpec) -> None:
    with pytest.raises(InvalidOrder):
        _long(spec, volume=0.0)


def test_el_stop_solo_se_mueve_a_favor_por_defecto(spec: InstrumentSpec) -> None:
    position = _long(spec)
    position.move_stop(1998.0)
    assert position.stop_loss == pytest.approx(1998.0)
    with pytest.raises(InvalidOrder):
        position.move_stop(1996.0)


def test_el_stop_puede_ampliarse_si_se_pide_explicitamente(spec: InstrumentSpec) -> None:
    position = _long(spec)
    position.move_stop(1990.0, allow_adverse=True)
    assert position.stop_loss == pytest.approx(1990.0)


def test_break_even_es_un_movimiento_valido(spec: InstrumentSpec) -> None:
    position = _long(spec)
    position.move_stop(2000.5)  # por encima de la entrada: beneficio asegurado
    assert position.stop_loss == pytest.approx(2000.5)


def test_no_se_opera_sobre_una_posicion_cerrada(spec: InstrumentSpec) -> None:
    position = _long(spec)
    position.close(
        spec=spec,
        exit_price=2005.0,
        exit_time=datetime(2024, 3, 4, 11, 0, tzinfo=UTC),
        exit_index=14,
        reason=ExitReason.TAKE_PROFIT,
    )
    with pytest.raises(PositionAlreadyClosed):
        position.move_stop(2001.0)


# --- Excursiones y riesgo ---------------------------------------------------


def test_mae_y_mfe_de_un_largo(spec: InstrumentSpec) -> None:
    position = _long(spec)
    position.update_excursion(high=2008.0, low=1997.0)
    position.update_excursion(high=2003.0, low=1996.0)
    assert position.mfe(spec) == pytest.approx(800.0)  # +8 USD * 100 oz
    assert position.mae(spec) == pytest.approx(-400.0)  # -4 USD * 100 oz


def test_riesgo_inicial_no_cambia_al_mover_el_stop(spec: InstrumentSpec) -> None:
    position = _long(spec)
    riesgo_original = position.risk_amount(spec)
    position.move_stop(1999.0)
    assert position.risk_amount(spec) == pytest.approx(riesgo_original)
    assert riesgo_original == pytest.approx(500.0)


def test_sin_stop_no_hay_riesgo_definido(spec: InstrumentSpec) -> None:
    position = _long(spec, stop_loss=None)
    assert position.risk_amount(spec) is None


# --- Cierre y P&L -----------------------------------------------------------


def test_el_trade_agrega_bruto_comision_y_swap(spec: InstrumentSpec) -> None:
    position = _long(spec, commission_paid=-3.0)
    position.accrue_swap(-8.0)
    trade = position.close(
        spec=spec,
        exit_price=2005.0,
        exit_time=datetime(2024, 3, 5, 10, 0, tzinfo=UTC),
        exit_index=100,
        reason=ExitReason.TAKE_PROFIT,
        exit_commission=-3.0,
    )
    assert trade.gross_pnl == pytest.approx(500.0)
    assert trade.commission == pytest.approx(-6.0)
    assert trade.swap == pytest.approx(-8.0)
    assert trade.net_pnl == pytest.approx(486.0)
    assert trade.r_multiple == pytest.approx(486.0 / 500.0)


def test_el_pnl_flotante_es_bruto(spec: InstrumentSpec) -> None:
    # El bróker ya descontó comisión y swap del balance: incluirlos aquí
    # los contaría dos veces en el equity.
    position = _long(spec, commission_paid=-3.0)
    assert position.unrealized_pnl(spec, 2001.0) == pytest.approx(100.0)
    assert position.unrealized_pnl_net(spec, 2001.0) == pytest.approx(97.0)


# --- Cuenta -----------------------------------------------------------------


def test_equity_y_drawdown(account: Account) -> None:
    account.mark_to_market(floating_pnl=500.0, used_margin=1000.0)
    assert account.equity == pytest.approx(10_500.0)
    assert account.peak_equity == pytest.approx(10_500.0)

    account.mark_to_market(floating_pnl=-1_000.0, used_margin=1000.0)
    assert account.drawdown == pytest.approx(1_500.0)
    assert account.drawdown_pct == pytest.approx(1_500.0 / 10_500.0)


def test_nivel_de_margen_y_stop_out(account: Account) -> None:
    assert account.margin_level is None  # sin posiciones no hay nivel

    account.mark_to_market(floating_pnl=-9_600.0, used_margin=1_000.0)
    assert account.margin_level == pytest.approx(0.4)
    assert account.is_stopped_out()
    assert account.is_margin_called()


def test_margen_libre_insuficiente(account: Account) -> None:
    account.mark_to_market(floating_pnl=0.0, used_margin=9_500.0)
    with pytest.raises(InsufficientMargin):
        account.require_margin(1_000.0)


def test_balance_inicial_invalido() -> None:
    with pytest.raises(DomainError):
        Account(initial_balance=0.0)


# --- Señales ----------------------------------------------------------------


def test_la_señal_resuelve_niveles_sobre_el_precio_real() -> None:
    signal = EntrySignal(side=Side.BUY, stop_distance=5.0, take_profit_distance=10.0)
    stop, target = signal.levels(2001.0)
    assert stop == pytest.approx(1996.0)
    assert target == pytest.approx(2011.0)


def test_la_señal_corta_invierte_las_distancias() -> None:
    signal = EntrySignal(side=Side.SELL, stop_distance=5.0, take_profit_distance=10.0)
    stop, target = signal.levels(2000.0)
    assert stop == pytest.approx(2005.0)
    assert target == pytest.approx(1990.0)


def test_no_se_puede_dar_stop_como_precio_y_distancia() -> None:
    with pytest.raises(InvalidOrder):
        EntrySignal(side=Side.BUY, stop_loss=1995.0, stop_distance=5.0)


def test_niveles_del_lado_equivocado_se_rechazan() -> None:
    signal = EntrySignal(side=Side.BUY, stop_loss=2010.0)
    with pytest.raises(InvalidOrder):
        signal.validate_against(2000.0)
