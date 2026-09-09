"""Especificación del instrumento: la única fuente de verdad sobre precios,
volúmenes y costes. Ningún cálculo monetario debe hacerse fuera de aquí.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import time

from chronos.domain.enums import Side
from chronos.domain.errors import InvalidInstrumentSpec, InvalidVolume


@dataclass(frozen=True, slots=True)
class CostModel:
    """Costes de transacción tal como los aplica el bróker."""

    spread_mode: str = "fixed"  # fixed | from_data
    spread_points: float = 20.0
    commission_per_lot_per_side: float = 3.0
    slippage_points: float = 1.0

    def __post_init__(self) -> None:
        if self.spread_mode not in ("fixed", "from_data"):
            raise InvalidInstrumentSpec(f"spread_mode desconocido: {self.spread_mode}")
        if self.spread_points < 0 or self.commission_per_lot_per_side < 0:
            raise InvalidInstrumentSpec("Los costes no pueden ser negativos")
        if self.slippage_points < 0:
            raise InvalidInstrumentSpec("El slippage no puede ser negativo")


@dataclass(frozen=True, slots=True)
class SwapModel:
    """Financiación nocturna. `points` = ticks del símbolo por lote y noche."""

    mode: str = "points"
    long_points: float = 0.0
    short_points: float = 0.0
    triple_swap_weekday: int = 2  # 0=lunes ... 4=viernes
    rollover_hour_server: int = 0

    def __post_init__(self) -> None:
        if self.mode != "points":
            raise InvalidInstrumentSpec(f"Modo de swap no soportado: {self.mode}")
        if not 0 <= self.triple_swap_weekday <= 6:
            raise InvalidInstrumentSpec("triple_swap_weekday debe estar entre 0 y 6")
        if not 0 <= self.rollover_hour_server <= 23:
            raise InvalidInstrumentSpec("rollover_hour_server debe estar entre 0 y 23")

    def points_for(self, side: Side, weekday: int) -> float:
        """Puntos de swap para un lado en un día concreto (triple incluido)."""
        base = self.long_points if side is Side.BUY else self.short_points
        multiplier = 3.0 if weekday == self.triple_swap_weekday else 1.0
        return base * multiplier


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Ventana de negociación del símbolo, en hora del servidor del bróker."""

    timezone: str = "Europe/Athens"
    break_start: time | None = None
    break_end: time | None = None
    week_open_weekday: int = 6
    week_open_time: time = time(1, 5)
    week_close_weekday: int = 4
    week_close_time: time = time(23, 55)

    def is_in_daily_break(self, moment: time) -> bool:
        """El corte diario puede cruzar medianoche; se trata como intervalo circular."""
        if self.break_start is None or self.break_end is None:
            return False
        if self.break_start <= self.break_end:
            return self.break_start <= moment < self.break_end
        return moment >= self.break_start or moment < self.break_end


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Ficha del símbolo. Encapsula toda la aritmética de precio/volumen/dinero."""

    symbol: str
    digits: int = 2
    tick_size: float = 0.01
    pip_size: float = 0.01
    contract_size: float = 100.0
    base_currency: str = "XAU"
    quote_currency: str = "USD"
    profit_currency: str = "USD"
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    leverage: float = 200.0
    costs: CostModel = field(default_factory=CostModel)
    swap: SwapModel = field(default_factory=SwapModel)
    session: SessionSpec = field(default_factory=SessionSpec)

    def __post_init__(self) -> None:
        if self.tick_size <= 0 or self.pip_size <= 0:
            raise InvalidInstrumentSpec("tick_size y pip_size deben ser positivos")
        if self.contract_size <= 0:
            raise InvalidInstrumentSpec("contract_size debe ser positivo")
        if not 0 < self.min_lot <= self.max_lot:
            raise InvalidInstrumentSpec("Rango de lotaje inválido")
        if self.lot_step <= 0:
            raise InvalidInstrumentSpec("lot_step debe ser positivo")
        if self.leverage <= 0:
            raise InvalidInstrumentSpec("leverage debe ser positivo")

    # --- Precio -------------------------------------------------------------

    def round_price(self, price: float) -> float:
        """Ajusta un precio a la rejilla de ticks del símbolo."""
        return round(round(price / self.tick_size) * self.tick_size, self.digits)

    def points_to_price(self, points: float) -> float:
        """Convierte puntos (ticks) a una distancia de precio."""
        return points * self.tick_size

    def price_to_points(self, price_delta: float) -> float:
        """Convierte una distancia de precio a puntos (ticks)."""
        return price_delta / self.tick_size

    def price_to_pips(self, price_delta: float) -> float:
        return price_delta / self.pip_size

    # --- Volumen ------------------------------------------------------------

    def normalize_volume(self, lots: float) -> float:
        """Ajusta el volumen al lot step y a los límites del símbolo.

        Redondea hacia abajo: nunca arriesga más de lo pedido. Devuelve 0.0 si
        el resultado no alcanza el volumen mínimo operable.
        """
        if lots <= 0:
            return 0.0
        steps = math.floor(lots / self.lot_step + 1e-9)
        normalized = round(steps * self.lot_step, 8)
        if normalized < self.min_lot:
            return 0.0
        return min(normalized, self.max_lot)

    def validate_volume(self, lots: float) -> None:
        if lots < self.min_lot or lots > self.max_lot:
            raise InvalidVolume(
                f"{lots} fuera del rango operable [{self.min_lot}, {self.max_lot}]"
            )

    # --- Dinero -------------------------------------------------------------

    @property
    def value_per_point_per_lot(self) -> float:
        """Beneficio en divisa de cuenta por 1 tick de movimiento y 1 lote."""
        return self.contract_size * self.tick_size

    def gross_pnl(self, side: Side, entry: float, exit_price: float, lots: float) -> float:
        """P&L bruto (sin comisiones ni swap) en divisa de beneficio."""
        return side.sign * (exit_price - entry) * self.contract_size * lots

    def price_delta_for_amount(self, amount: float, lots: float) -> float:
        """Distancia de precio que equivale a `amount` de P&L con `lots` lotes."""
        if lots <= 0:
            raise InvalidVolume("lots debe ser positivo")
        return amount / (self.contract_size * lots)

    def lots_for_risk(self, risk_amount: float, stop_distance: float) -> float:
        """Lotaje cuyo riesgo, si salta el stop, equivale a `risk_amount`.

        `stop_distance` es la distancia de precio entre entrada y stop loss.
        """
        if stop_distance <= 0:
            raise InvalidVolume("La distancia al stop debe ser positiva")
        if risk_amount <= 0:
            return 0.0
        raw = risk_amount / (stop_distance * self.contract_size)
        return self.normalize_volume(raw)

    def commission(self, lots: float, sides: int = 2) -> float:
        """Comisión total en divisa de cuenta. `sides=2` = apertura + cierre."""
        return self.costs.commission_per_lot_per_side * lots * sides

    def swap_charge(self, side: Side, lots: float, weekday: int) -> float:
        """Cargo de swap (negativo = coste) por una noche mantenida."""
        return self.swap.points_for(side, weekday) * self.value_per_point_per_lot * lots

    def margin_required(self, lots: float, price: float) -> float:
        """Margen necesario para abrir `lots` al precio dado."""
        return (lots * self.contract_size * price) / self.leverage

    @property
    def half_spread(self) -> float:
        """Media horquilla en precio (modelo de spread fijo)."""
        return self.points_to_price(self.costs.spread_points) / 2.0
