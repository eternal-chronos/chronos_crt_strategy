"""Configuración del explorador: qué pares se dibujan, con qué velas y cómo.

Dataclasses puras; la tolerancia al YAML vive en infraestructura. No hay ni un
parámetro de estrategia: lo que se declara aquí es el histórico, la rejilla de
agregación y qué se dibuja, nada de qué se decide.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import time

from chronos.domain.errors import DomainError

#: Temporalidades que el explorador sabe construir desde un histórico M1.
M15 = "M15"
H1 = "H1"
H4 = "H4"
DAILY = "D"

SUPPORTED_TIMEFRAMES = (M15, H1, H4, DAILY)

TIMEFRAME_MINUTES: dict[str, int] = {M15: 15, H1: 60, H4: 240, DAILY: 1440}

#: Cómo se escribe cada temporalidad en los controles y en la leyenda.
TIMEFRAME_LABELS: dict[str, str] = {DAILY: "Diario", H4: "H4", H1: "H1", M15: "M15"}

#: Tecla que salta a cada gráfico. Va aquí y no en el JavaScript porque el
#: explorador dibuja sólo las temporalidades que la configuración pide.
TIMEFRAME_KEYS: dict[str, str] = {DAILY: "d", H4: "4", H1: "1", M15: "m"}


def by_size(timeframes: Iterable[str], *, descending: bool = True) -> tuple[str, ...]:
    """Ordena temporalidades por duración, de mayor a menor por defecto."""
    return tuple(
        sorted(set(timeframes), key=lambda name: TIMEFRAME_MINUTES[name], reverse=descending)
    )


@dataclass(frozen=True, slots=True)
class SymbolConfig:
    """Un par del explorador: de dónde salen sus velas y cómo se escribe su precio.

    `decimals` es la precisión con la que se DIBUJA —cuántas cifras lleva el
    precio en el globo, en el eje y en las marcas a mano— y de ella sale también
    el pip con el que se miden las distancias: la última cifra que se enseña. No
    tiene nada que ver con los decimales con los que Dukascopy codifica el
    fichero crudo, que son cosa del descargador.
    """

    symbol: str
    #: Un fichero por lado. `bid_path` es el mínimo para poder dibujar.
    bid_path: str = ""
    ask_path: str = ""
    #: Alternativa: un único fichero con columnas `bid_*` / `ask_*`, o un OHLC
    #: suelto que se toma como el lado declarado.
    path: str = ""
    timezone: str = "UTC"
    start: str | None = None
    end: str | None = None
    decimals: int = 5
    #: Cómo se escribe el par en el selector. Vacío = el propio símbolo.
    label: str = ""

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise DomainError("Un par del explorador tiene que declarar su símbolo")
        if not 0 <= self.decimals <= 8:
            raise DomainError(f"decimals fuera de rango en {self.symbol}: {self.decimals}")

    @property
    def is_declared(self) -> bool:
        """`False` mientras no haya ninguna ruta: el par se salta en vez de fallar."""
        return bool(self.path or self.bid_path)

    @property
    def name(self) -> str:
        return self.label or self.symbol


#: Plazas cuya sesión puede anclar el día. La clave es el prefijo que se escribe
#: en `d_session_start`; el valor, la zona IANA con la que se resuelve el horario
#: de verano de verdad, año por año.
SESSION_TIMEZONES: dict[str, str] = {"NY": "America/New_York"}

_SESSION_START = re.compile(r"^(?P<place>[A-Z]{2,4})_(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


@dataclass(frozen=True, slots=True)
class SessionAnchor:
    """Arranque del día pegado a la hora local de una plaza, con DST real.

    Un desplazamiento fijo en UTC no puede reproducir un gráfico anclado a la
    sesión: Nueva York abre a la misma hora local todo el año, así que en UTC el
    corte se mueve una hora dos veces al año. Con este ancla el corte se calcula
    convirtiendo a la zona de la plaza, no sumando horas.
    """

    #: Prefijo escrito en la configuración (`NY`).
    place: str
    #: Zona IANA con la que se resuelve el horario de verano.
    timezone: str
    #: Hora local de apertura de la sesión.
    at: time

    @property
    def label(self) -> str:
        return f"{self.place}_{self.at.hour:02d}:{self.at.minute:02d}"

    def describe(self) -> str:
        return f"{self.at.hour:02d}:{self.at.minute:02d} de {self.timezone} (DST real)"


@dataclass(frozen=True, slots=True)
class AggregationConfig:
    """Agregación del histórico a las temporalidades del explorador.

    Las velas dependen íntegramente de estos dos valores: cambiarlos cambia lo
    que se ve. Sólo H4 y el diario admiten desplazamiento, que es donde las
    plataformas discrepan; la rejilla de M15 y H1 —cuartos de hora y horas en
    punto— es la misma en todas y no lleva parámetro que ajustar.
    """

    #: Desplazamiento del inicio de las velas H4 respecto a 00:00 UTC. **Se ignora
    #: cuando `d_session_start` ancla el día a una sesión**: ahí H4 arranca con la
    #: sesión, que es lo que hace cTrader.
    h4_offset_hours: int = 0
    #: Arranque de la vela diaria. Dos formas:
    #:   `HH:MM`      -> hora fija en UTC (`"00:00"`, `"22:00"`);
    #:   `PLAZA_HH:MM`-> hora local de una plaza con horario de verano real
    #:                   (`"NY_17:00"`, `"NY_18:00"`).
    d_session_start: str = "NY_17:00"

    def __post_init__(self) -> None:
        if not 0 <= self.h4_offset_hours <= 23:
            raise DomainError("h4_offset_hours debe estar en [0, 23]")
        self.session_start_time()  # valida el formato al construir

    @property
    def h4_effective_offset_hours(self) -> int:
        """Desplazamiento efectivo dentro del ciclo de 4 horas."""
        return self.h4_offset_hours % 4

    @property
    def session_anchor(self) -> SessionAnchor | None:
        """Ancla de sesión, o `None` si el corte es una hora fija en UTC."""
        match = _SESSION_START.match(self.d_session_start)
        if match is None:
            return None
        place = match.group("place")
        timezone = SESSION_TIMEZONES.get(place)
        if timezone is None:
            raise DomainError(
                f"Plaza desconocida en d_session_start: {place!r}. "
                f"Disponibles: {', '.join(sorted(SESSION_TIMEZONES))}"
            )
        return SessionAnchor(
            place=place,
            timezone=timezone,
            at=_parse_time(match.group("hour"), match.group("minute")),
        )

    def session_start_time(self) -> time:
        """Hora de arranque del día, local a su plaza si hay ancla de sesión."""
        anchor = self.session_anchor
        if anchor is not None:
            return anchor.at
        parts = self.d_session_start.split(":")
        if len(parts) != 2:
            raise DomainError(
                f"d_session_start debe ser HH:MM o PLAZA_HH:MM, no {self.d_session_start!r}"
            )
        return _parse_time(*parts)

    def describe_daily_start(self) -> str:
        """Cómo se imprime el corte diario en los informes."""
        anchor = self.session_anchor
        return anchor.describe() if anchor is not None else f"{self.d_session_start} UTC"


def _parse_time(hour: str, minute: str) -> time:
    try:
        return time(hour=int(hour), minute=int(minute))
    except ValueError as error:
        raise DomainError(f"Hora inválida en d_session_start: {hour}:{minute}") from error


@dataclass(frozen=True, slots=True)
class MarksConfig:
    """Los nombres con los que el propietario marca a mano sobre el gráfico.

    Son ETIQUETAS DE DIBUJO y nada más: ningún cálculo las lee, no salen del
    HTML y el motor no se entera de que existen. Se declaran en la configuración
    —y no en el JavaScript— porque el vocabulario cambia con la estrategia que se
    esté escribiendo, y renombrarlas no puede exigir tocar el explorador.

    Como mucho tres de cada: los colores de la mano son tres, elegidos para no
    confundirse con nada de lo que pinte un motor, y un cuarto tendría que
    repetir uno.
    """

    #: Recuadros: se plantan con un clic y se arrastran por bordes y por dentro.
    rects: tuple[str, ...] = ("Zona 1", "Zona 2", "Zona 3")
    #: Líneas: nacen horizontales al precio pulsado y se inclinan por los extremos.
    lines: tuple[str, ...] = ("Nivel 1", "Nivel 2", "Nivel 3")

    def __post_init__(self) -> None:
        for name, items in (("rects", self.rects), ("lines", self.lines)):
            if not items:
                raise DomainError(f"marks.{name} no puede quedarse vacío")
            if len(items) > 3:
                raise DomainError(
                    f"marks.{name} admite tres nombres como mucho: hay tres colores de mano"
                )
            if len(set(items)) != len(items):
                raise DomainError(f"marks.{name} repite algún nombre: {items}")
            for item in items:
                if not item.strip():
                    raise DomainError(f"marks.{name} tiene un nombre vacío")


@dataclass(frozen=True, slots=True)
class TimezoneAuditConfig:
    """Verificación empírica de zona horaria. Obligatoria antes de dibujar nada."""

    enabled: bool = True
    #: Hora UTC en la que debe verse el pico de volatilidad (datos macro de EE. UU.).
    expected_peak_utc: str = "13:30"
    tolerance_minutes: int = 30
    #: Horas de hueco a partir de las cuales se considera parada de fin de semana.
    weekend_gap_hours: float = 12.0
    #: Fracción mínima de huecos que deben empezar en viernes y acabar en domingo.
    #: No es 1.0 porque los festivos también generan paradas largas.
    min_conformity: float = 0.9

    def __post_init__(self) -> None:
        if not 0 < self.min_conformity <= 1:
            raise DomainError("min_conformity debe estar en (0, 1]")
        if self.tolerance_minutes < 0:
            raise DomainError("tolerance_minutes no puede ser negativo")


@dataclass(frozen=True, slots=True)
class ExplorerReportingConfig:
    """Dónde se escribe el explorador y cuánto histórico se embebe."""

    output_dir: str = "now/explorador"
    #: Zona horaria de la sesión del propietario; el explorador imprime ambas.
    session_timezone: str = "Etc/GMT+4"
    #: Por temporalidad y por par; se conservan las más recientes. 0 = todas.
    #:
    #: El HTML lleva las velas dentro, así que este número es el tamaño del
    #: fichero: cuatro pares por cuatro temporalidades multiplican lo que en el
    #: explorador de un solo símbolo era una cifra cómoda.
    max_explorer_bars: int = 40_000

    def __post_init__(self) -> None:
        if self.max_explorer_bars < 0:
            raise DomainError("max_explorer_bars no puede ser negativo")


@dataclass(frozen=True, slots=True)
class ExplorerConfig:
    """Configuración completa del explorador."""

    #: Lado del precio con el que se dibuja: bid | ask | mid.
    price_side: str = "bid"
    #: Gráficos disponibles, de mayor a menor. El primero es el que abre.
    timeframes: tuple[str, ...] = (DAILY, H4, H1, M15)
    symbols: tuple[SymbolConfig, ...] = ()
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)
    marks: MarksConfig = field(default_factory=MarksConfig)
    timezone_audit: TimezoneAuditConfig = field(default_factory=TimezoneAuditConfig)
    reporting: ExplorerReportingConfig = field(default_factory=ExplorerReportingConfig)

    def __post_init__(self) -> None:
        if self.price_side not in ("bid", "ask", "mid"):
            raise DomainError(f"price_side desconocido: {self.price_side}")
        if not self.timeframes:
            raise DomainError("Hay que declarar al menos una temporalidad")
        for timeframe in self.timeframes:
            if timeframe not in SUPPORTED_TIMEFRAMES:
                raise DomainError(
                    f"Temporalidad no soportada: {timeframe}. "
                    f"Disponibles: {', '.join(SUPPORTED_TIMEFRAMES)}"
                )
        if len(set(self.timeframes)) != len(self.timeframes):
            raise DomainError(f"Hay temporalidades repetidas: {self.timeframes}")
        if not self.symbols:
            raise DomainError("Hay que declarar al menos un par")
        names = [symbol.symbol.upper() for symbol in self.symbols]
        if len(set(names)) != len(names):
            raise DomainError(f"Hay pares repetidos: {', '.join(names)}")

    @property
    def ordered_timeframes(self) -> tuple[str, ...]:
        """Las temporalidades de mayor a menor, que es el orden de los botones."""
        return by_size(self.timeframes)

    @property
    def declared(self) -> tuple[SymbolConfig, ...]:
        """Los pares que tienen histórico declarado: los demás no se pueden dibujar."""
        return tuple(symbol for symbol in self.symbols if symbol.is_declared)

    def find(self, symbol: str) -> SymbolConfig:
        key = symbol.strip().upper()
        for item in self.symbols:
            if item.symbol.upper() == key:
                return item
        available = ", ".join(item.symbol for item in self.symbols)
        raise DomainError(f"Par no declarado en la configuración: {symbol}. Hay: {available}")
