"""Ejecución simulada: horquilla, costes, stops intrabar y huecos."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chronos.application.backtest.config import ExecutionConfig
from chronos.domain.account import Account
from chronos.domain.enums import ExitReason, Side
from chronos.domain.instrument import InstrumentSpec
from chronos.infrastructure.broker.simulated import SimulatedBroker

MOMENT = datetime(2024, 3, 4, 10, 0, tzinfo=UTC)


def _broker(spec: InstrumentSpec, account: Account, **overrides: object) -> SimulatedBroker:
    execution = ExecutionConfig(**overrides)  # type: ignore[arg-type]
    return SimulatedBroker(spec, account, execution)


# --- Horquilla --------------------------------------------------------------


def test_los_datos_bid_generan_el_ask_sumando_la_horquilla(
    spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(spec, account, price_basis="bid")
    quote = broker.quote(2000.0)
    assert quote.bid == pytest.approx(2000.0)
    assert quote.ask == pytest.approx(2000.20)


def test_los_datos_mid_reparten_la_horquilla(spec: InstrumentSpec, account: Account) -> None:
    broker = _broker(spec, account, price_basis="mid")
    quote = broker.quote(2000.0)
    assert quote.bid == pytest.approx(1999.90)
    assert quote.ask == pytest.approx(2000.10)


def test_se_compra_en_ask_y_se_vende_en_bid(spec: InstrumentSpec, account: Account) -> None:
    broker = _broker(spec, account)
    largo = broker.open_position(
        side=Side.BUY, volume=0.1, quote=broker.quote(2000.0), timestamp=MOMENT, index=0
    )
    corto = broker.open_position(
        side=Side.SELL, volume=0.1, quote=broker.quote(2000.0), timestamp=MOMENT, index=0
    )
    assert largo.entry_price == pytest.approx(2000.20)
    assert corto.entry_price == pytest.approx(2000.00)


def test_la_horquilla_se_puede_desactivar(spec: InstrumentSpec, account: Account) -> None:
    broker = _broker(spec, account, apply_spread=False)
    assert broker.quote(2000.0).ask == pytest.approx(2000.0)


def test_una_ida_y_vuelta_inmediata_pierde_horquilla_y_comision(
    spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(spec, account)
    position = broker.open_position(
        side=Side.BUY, volume=1.0, quote=broker.quote(2000.0), timestamp=MOMENT, index=0
    )
    trade = broker.close_at_market(
        position, quote=broker.quote(2000.0), timestamp=MOMENT, index=1, reason=ExitReason.SIGNAL
    )
    # 0.20 de horquilla * 100 oz = -20 USD, más 3 USD por lado de comisión.
    assert trade.gross_pnl == pytest.approx(-20.0)
    assert trade.commission == pytest.approx(-6.0)
    assert trade.net_pnl == pytest.approx(-26.0)
    assert account.balance == pytest.approx(10_000.0 - 26.0)


# --- Stops intrabar ---------------------------------------------------------


def test_el_stop_de_un_largo_salta_con_el_minimo_de_la_barra(
    no_cost_spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(no_cost_spec, account)
    broker.open_position(
        side=Side.BUY,
        volume=1.0,
        quote=broker.quote(2000.0),
        timestamp=MOMENT,
        index=0,
        stop_loss=1995.0,
    )
    trades = broker.process_protective_orders(
        open_price=2000.0, high=2002.0, low=1994.0, spread=None, timestamp=MOMENT, index=1
    )
    assert len(trades) == 1
    assert trades[0].reason is ExitReason.STOP_LOSS
    assert trades[0].exit_price == pytest.approx(1995.0)


def test_un_hueco_ejecuta_el_stop_en_la_apertura_no_en_el_nivel(
    no_cost_spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(no_cost_spec, account)
    broker.open_position(
        side=Side.BUY,
        volume=1.0,
        quote=broker.quote(2000.0),
        timestamp=MOMENT,
        index=0,
        stop_loss=1995.0,
    )
    # La barra abre por debajo del stop: el fill real es peor que el nivel.
    trades = broker.process_protective_orders(
        open_price=1990.0, high=1992.0, low=1988.0, spread=None, timestamp=MOMENT, index=1
    )
    assert trades[0].exit_price == pytest.approx(1990.0)


def test_el_objetivo_se_llena_al_nivel(no_cost_spec: InstrumentSpec, account: Account) -> None:
    broker = _broker(no_cost_spec, account)
    broker.open_position(
        side=Side.BUY,
        volume=1.0,
        quote=broker.quote(2000.0),
        timestamp=MOMENT,
        index=0,
        take_profit=2010.0,
    )
    trades = broker.process_protective_orders(
        open_price=2001.0, high=2012.0, low=2000.0, spread=None, timestamp=MOMENT, index=1
    )
    assert trades[0].reason is ExitReason.TAKE_PROFIT
    assert trades[0].exit_price == pytest.approx(2010.0)


def test_si_la_barra_toca_ambos_gana_el_stop(no_cost_spec: InstrumentSpec, account: Account) -> None:
    broker = _broker(no_cost_spec, account, intrabar_priority="worst")
    broker.open_position(
        side=Side.BUY,
        volume=1.0,
        quote=broker.quote(2000.0),
        timestamp=MOMENT,
        index=0,
        stop_loss=1995.0,
        take_profit=2010.0,
    )
    trades = broker.process_protective_orders(
        open_price=2000.0, high=2012.0, low=1990.0, spread=None, timestamp=MOMENT, index=1
    )
    assert trades[0].reason is ExitReason.STOP_LOSS


def test_la_prioridad_optimista_es_configurable(
    no_cost_spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(no_cost_spec, account, intrabar_priority="tp_first")
    broker.open_position(
        side=Side.BUY,
        volume=1.0,
        quote=broker.quote(2000.0),
        timestamp=MOMENT,
        index=0,
        stop_loss=1995.0,
        take_profit=2010.0,
    )
    trades = broker.process_protective_orders(
        open_price=2000.0, high=2012.0, low=1990.0, spread=None, timestamp=MOMENT, index=1
    )
    assert trades[0].reason is ExitReason.TAKE_PROFIT


def test_el_stop_de_un_corto_se_evalua_contra_el_ask(
    spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(spec, account)
    broker.open_position(
        side=Side.SELL,
        volume=1.0,
        quote=broker.quote(2000.0),
        timestamp=MOMENT,
        index=0,
        stop_loss=2005.0,
    )
    # El máximo bid es 2004.9, pero el ask llega a 2005.1: el stop salta.
    trades = broker.process_protective_orders(
        open_price=2000.0, high=2004.9, low=1999.0, spread=None, timestamp=MOMENT, index=1
    )
    assert len(trades) == 1
    assert trades[0].reason is ExitReason.STOP_LOSS


def test_el_deslizamiento_empeora_el_stop(spec: InstrumentSpec, account: Account) -> None:
    from chronos.domain.instrument import CostModel

    spec_con_slippage = InstrumentSpec(
        symbol=spec.symbol,
        costs=CostModel(spread_points=0.0, commission_per_lot_per_side=0.0, slippage_points=10.0),
    )
    broker = _broker(spec_con_slippage, account)
    broker.open_position(
        side=Side.BUY,
        volume=1.0,
        quote=broker.quote(2000.0),
        timestamp=MOMENT,
        index=0,
        stop_loss=1995.0,
    )
    trades = broker.process_protective_orders(
        open_price=1999.0, high=1999.0, low=1994.0, spread=None, timestamp=MOMENT, index=1
    )
    assert trades[0].exit_price == pytest.approx(1994.90)  # 10 puntos peor


# --- Swap, margen y stop out ------------------------------------------------


def test_el_swap_se_carga_al_balance_y_a_la_posicion(
    spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(spec, account)
    position = broker.open_position(
        side=Side.BUY, volume=1.0, quote=broker.quote(2000.0), timestamp=MOMENT, index=0
    )
    balance_previo = account.balance
    cargo = broker.apply_swap(weekday=0)
    assert cargo == pytest.approx(-8.0)
    assert position.swap_accrued == pytest.approx(-8.0)
    assert account.balance == pytest.approx(balance_previo - 8.0)


def test_el_margen_bloquea_posiciones_demasiado_grandes(
    spec: InstrumentSpec, account: Account
) -> None:
    broker = _broker(spec, account)
    assert broker.can_afford(1.0, 2000.0) is True  # 1000 USD de margen
    assert broker.can_afford(50.0, 2000.0) is False  # 50 000 USD


def test_el_stop_out_liquida_todo(spec: InstrumentSpec, account: Account) -> None:
    broker = _broker(spec, account)
    broker.open_position(
        side=Side.BUY, volume=5.0, quote=broker.quote(2000.0), timestamp=MOMENT, index=0
    )
    quote_caida = broker.quote(1980.0)
    broker.mark_to_market(quote_caida)
    cerrados = broker.enforce_stop_out(quote=quote_caida, timestamp=MOMENT, index=1)
    assert len(cerrados) == 1
    assert cerrados[0].reason is ExitReason.STOP_OUT
    assert broker.positions == []
