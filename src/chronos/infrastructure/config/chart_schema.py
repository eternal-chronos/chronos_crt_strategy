"""Esquema Pydantic del YAML del explorador.

Toda la tolerancia al mundo exterior vive aquí. Lo que se escribe mal es un
error de configuración, no un valor por defecto silencioso: `extra="forbid"`
convierte una clave con una errata en un fallo con nombre y no en un ajuste que
no se aplica.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chronos.application.chart.config import (
    AggregationConfig,
    ExplorerConfig,
    ExplorerReportingConfig,
    MarksConfig,
    SymbolConfig,
    TimezoneAuditConfig,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SymbolSchema(_Strict):
    symbol: str
    bid_path: str = ""
    ask_path: str = ""
    path: str = ""
    timezone: str = "UTC"
    start: str | None = None
    end: str | None = None
    #: Cifras con las que se DIBUJA el precio, y de las que sale el pip.
    decimals: int = Field(default=5, ge=0, le=8)
    label: str = ""
    #: Gráficos de este par. Vacío = los `timeframes` generales.
    timeframes: list[str] = Field(default_factory=list)

    def to_domain(self) -> SymbolConfig:
        values = self.model_dump()
        values["timeframes"] = tuple(self.timeframes)
        return SymbolConfig(**values)


class AggregationSchema(_Strict):
    h4_offset_hours: int = Field(default=0, ge=0, le=23)
    #: La rejilla de cTrader/Pepperstone, que es donde se opera en vivo. Ancla
    #: también el origen de H4: con un ancla de sesión, `h4_offset_hours` no
    #: pinta nada.
    d_session_start: str = "NY_17:00"

    def to_domain(self) -> AggregationConfig:
        return AggregationConfig(**self.model_dump())


class MarksSchema(_Strict):
    #: Los nombres con los que se marca a mano. Sólo son etiquetas de dibujo:
    #: cámbialos cuando la estrategia tenga vocabulario propio.
    rects: list[str] = Field(default_factory=lambda: ["Zona 1", "Zona 2", "Zona 3"])
    lines: list[str] = Field(default_factory=lambda: ["Nivel 1", "Nivel 2", "Nivel 3"])

    def to_domain(self) -> MarksConfig:
        return MarksConfig(rects=tuple(self.rects), lines=tuple(self.lines))


class TimezoneAuditSchema(_Strict):
    enabled: bool = True
    expected_peak_utc: str = "13:30"
    tolerance_minutes: int = Field(default=30, ge=0)
    weekend_gap_hours: float = Field(default=12.0, gt=0)
    min_conformity: float = Field(default=0.9, gt=0, le=1)

    def to_domain(self) -> TimezoneAuditConfig:
        return TimezoneAuditConfig(**self.model_dump())


class ExplorerReportingSchema(_Strict):
    output_dir: str = "now/explorador"
    session_timezone: str = "Etc/GMT+4"
    max_explorer_bars: int = Field(default=40_000, ge=0)

    def to_domain(self) -> ExplorerReportingConfig:
        return ExplorerReportingConfig(**self.model_dump())


class ExplorerSchema(_Strict):
    price_side: Literal["bid", "ask", "mid"] = "bid"
    timeframes: list[str] = Field(default_factory=lambda: ["D", "H4", "H1", "M15"])
    symbols: list[SymbolSchema] = Field(default_factory=list)
    aggregation: AggregationSchema = Field(default_factory=AggregationSchema)
    marks: MarksSchema = Field(default_factory=MarksSchema)
    timezone_audit: TimezoneAuditSchema = Field(default_factory=TimezoneAuditSchema)
    reporting: ExplorerReportingSchema = Field(default_factory=ExplorerReportingSchema)

    def to_domain(self) -> ExplorerConfig:
        return ExplorerConfig(
            price_side=self.price_side,
            timeframes=tuple(self.timeframes),
            symbols=tuple(item.to_domain() for item in self.symbols),
            aggregation=self.aggregation.to_domain(),
            marks=self.marks.to_domain(),
            timezone_audit=self.timezone_audit.to_domain(),
            reporting=self.reporting.to_domain(),
        )
