"""Descarga del histórico M1 de Dukascopy, con bid y ask separados.

Dukascopy publica un fichero por día, instrumento y lado en
`https://datafeed.dukascopy.com/datafeed/<SÍMBOLO>/<AAAA>/<MM-1>/<DD>/<LADO>_candles_min_1.bi5`.
Cada `.bi5` es un flujo LZMA (formato "alone") con registros de 24 bytes en
big-endian:

    int32   segundos desde las 00:00 UTC de ese día
    int32   open, close, low, high   (enteros; se dividen por 10^decimales)
    float32 volumen

El servidor limita el ritmo con 503 en cuanto se le pide demasiado seguido, así
que el cliente va con un regulador adaptativo y guarda en disco todo lo que
descarga: una descarga interrumpida se reanuda sin volver a pedir nada.

El calendario es UTC, y el módulo de estructura lo verifica de forma empírica
antes de calcular nada (`chronos structure verify-tz`).
"""

from __future__ import annotations

import calendar
import http.client
import lzma
import struct
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from chronos.domain.errors import DomainError

HOST = "datafeed.dukascopy.com"
SIDES = ("bid", "ask")

#: Bytes por registro y su desempaquetado.
_RECORD = struct.Struct(">5if")

#: Decimales con los que Dukascopy publica cada instrumento. Los precios vienen
#: como enteros y hay que dividirlos por 10^decimales.
_DECIMALS: dict[str, int] = {
    "XAUUSD": 3,
    "XAGUSD": 3,
    "EURUSD": 5,
    "GBPUSD": 5,
    "USDJPY": 3,
    "USDCHF": 5,
}
DEFAULT_DECIMALS = 5

#: Marcador de "este día no existe en el servidor": evita volver a pedirlo.
_MISSING = b""


class DukascopyError(DomainError):
    """La descarga no se pudo completar."""


def decimals_for(symbol: str) -> int:
    return _DECIMALS.get(symbol.upper(), DEFAULT_DECIMALS)


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    day: date
    side: str
    rows: int
    from_cache: bool
    done: int
    total: int


class _RateGuard:
    """Regulador adaptativo compartido por todos los hilos.

    Dukascopy no publica su límite, así que se descubre en marcha: cada 503
    separa más las peticiones y cada racha de aciertos las vuelve a juntar.
    """

    #: Aciertos seguidos antes de volver a apretar. Bajo a propósito: una
    #: descarga completa son unos cientos de peticiones, y un umbral alto deja
    #: el intervalo clavado en su techo durante toda la corrida por culpa de un
    #: par de rechazos al principio.
    _EASE_AFTER = 5

    def __init__(self, min_interval: float = 0.05, max_interval: float = 30.0) -> None:
        self._floor = min_interval
        self._ceiling = max_interval
        self._interval = min_interval
        self._next_slot = 0.0
        self._streak = 0
        self._lock = threading.Lock()

    @property
    def interval(self) -> float:
        return self._interval

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def penalize(self) -> None:
        with self._lock:
            self._streak = 0
            self._interval = min(self._ceiling, max(self._floor, self._interval * 2.0))
            self._next_slot = time.monotonic() + self._interval

    def reward(self) -> None:
        with self._lock:
            self._streak += 1
            if self._streak >= self._EASE_AFTER and self._interval > self._floor:
                self._streak = 0
                self._interval = max(self._floor, self._interval * 0.5)


class DukascopyClient:
    """Cliente HTTP con caché en disco, reintentos y regulación de ritmo."""

    def __init__(
        self,
        cache_dir: str | Path = "data/raw/dukascopy",
        *,
        timeout: float = 60.0,
        max_retries: int = 12,
        workers: int = 2,
        guard: _RateGuard | None = None,
    ) -> None:
        self._cache = Path(cache_dir)
        self._timeout = timeout
        self._max_retries = max_retries
        # Medido: el techo está en ~1 petición/s y no sube con más hilos, sólo
        # aparecen tiempos de espera agotados. Dos hilos cubren la latencia de
        # uno sin forzar la máquina.
        self._workers = max(1, workers)
        self._guard = guard or _RateGuard()
        self._local = threading.local()

    # --- Descarga -----------------------------------------------------------

    def fetch(self, symbol: str, side: str, day: date) -> bytes:
        """Contenido bruto del día (M1). Cadena vacía si el servidor no lo tiene."""
        return self._cached(self.cache_path(symbol, side, day), url_for(symbol, side, day))

    def fetch_month(self, symbol: str, side: str, month: date) -> bytes:
        """Contenido bruto del mes (H1)."""
        return self._cached(
            self.month_cache_path(symbol, side, month), month_url_for(symbol, side, month)
        )

    def cache_path(self, symbol: str, side: str, day: date) -> Path:
        return (
            self._cache
            / symbol.upper()
            / side.lower()
            / f"{day:%Y}"
            / f"{day:%m}"
            / f"{day:%d}.bi5"
        )

    def month_cache_path(self, symbol: str, side: str, month: date) -> Path:
        return (
            self._cache
            / symbol.upper()
            / side.lower()
            / "h1"
            / f"{month:%Y}"
            / f"{month:%m}.bi5"
        )

    def _cached(self, path: Path, url: str) -> bytes:
        if path.is_file():
            return path.read_bytes()
        payload = self._download(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return payload

    def _download(self, url: str) -> bytes:
        last_error: str = ""
        for attempt in range(self._max_retries):
            self._guard.wait()
            try:
                status, body = self._request(url)
            except OSError as error:  # socket, TLS, DNS...
                self._reset_connection()
                last_error = f"{type(error).__name__}: {error}"
                time.sleep(min(30.0, 2.0**attempt))
                continue

            if status == 200:
                self._guard.reward()
                return body
            if status == 404:
                # Día sin publicar (festivo, o anterior al histórico): no es un
                # error, es una ausencia legítima.
                self._guard.reward()
                return _MISSING
            if status in (429, 500, 502, 503, 504):
                self._guard.penalize()
                last_error = f"HTTP {status}"
                continue
            raise DukascopyError(f"{url} devolvió HTTP {status}")

        raise DukascopyError(
            f"No se pudo descargar {url} tras {self._max_retries} intentos ({last_error})"
        )

    def _request(self, url: str) -> tuple[int, bytes]:
        connection = self._connection()
        connection.request("GET", url, headers={"User-Agent": "chronos-strategy/0.1"})
        response = connection.getresponse()
        body = response.read()
        if response.will_close:
            self._reset_connection()
        return response.status, body

    def _connection(self) -> http.client.HTTPSConnection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            # Una conexión persistente por hilo: el coste del handshake TLS
            # domina frente a los 15 KB que pesa cada día.
            connection = http.client.HTTPSConnection(HOST, timeout=self._timeout)
            self._local.connection = connection
        return connection

    def _reset_connection(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
        self._local.connection = None

    def close(self) -> None:
        self._reset_connection()

    # --- Series -------------------------------------------------------------

    def download(
        self,
        symbol: str,
        start: date,
        end: date,
        side: str,
        *,
        on_progress: Callable[[DownloadProgress], None] | None = None,
    ) -> pd.DataFrame:
        """Histórico M1 de un lado, en el formato canónico del proyecto."""
        if start > end:
            raise DukascopyError("La fecha inicial es posterior a la final")
        if side.lower() not in SIDES:
            raise DukascopyError(f"Lado desconocido: {side}")

        days = list(_days_between(start, end))
        scale = 10.0 ** decimals_for(symbol)
        cached_before = {day: self.cache_path(symbol, side, day).is_file() for day in days}

        # La descarga se paraleliza y la descodificación no: así el orden de los
        # avisos de progreso es el del calendario y el resultado no depende de
        # cómo se hayan repartido los hilos.
        frames: list[pd.DataFrame] = []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            payloads = pool.map(lambda day: self.fetch(symbol, side, day), days)
            for position, (day, payload) in enumerate(zip(days, payloads, strict=True), start=1):
                frame = decode_minute_candles(payload, day, scale)
                if not frame.empty:
                    frames.append(frame)
                if on_progress is not None:
                    on_progress(
                        DownloadProgress(
                            day=day,
                            side=side,
                            rows=len(frame),
                            from_cache=cached_before[day],
                            done=position,
                            total=len(days),
                        )
                    )

        return _assemble(frames, symbol, start, end)

    def download_hourly(
        self,
        symbol: str,
        start: date,
        end: date,
        side: str,
        *,
        on_progress: Callable[[DownloadProgress], None] | None = None,
    ) -> pd.DataFrame:
        """Histórico H1 de un lado, un fichero por mes.

        Treinta veces menos peticiones que M1 para el mismo periodo, y con las
        mismas velas H4 y diarias al final (ver `decode_hour_candles`).
        """
        if start > end:
            raise DukascopyError("La fecha inicial es posterior a la final")
        if side.lower() not in SIDES:
            raise DukascopyError(f"Lado desconocido: {side}")

        months = list(_months_between(start, end))
        scale = 10.0 ** decimals_for(symbol)
        cached_before = {m: self.month_cache_path(symbol, side, m).is_file() for m in months}

        frames: list[pd.DataFrame] = []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            payloads = pool.map(lambda month: self.fetch_month(symbol, side, month), months)
            for position, (month, payload) in enumerate(
                zip(months, payloads, strict=True), start=1
            ):
                frame = decode_hour_candles(payload, month, scale)
                if not frame.empty:
                    frames.append(frame)
                if on_progress is not None:
                    on_progress(
                        DownloadProgress(
                            day=month,
                            side=side,
                            rows=len(frame),
                            from_cache=cached_before[month],
                            done=position,
                            total=len(months),
                        )
                    )

        history = _assemble(frames, symbol, start, end)
        # Los ficheros vienen por meses completos: se recorta a lo pedido.
        first = pd.Timestamp(datetime(start.year, start.month, start.day, tzinfo=UTC))
        last = pd.Timestamp(datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC))
        return history.loc[(history.index >= first) & (history.index <= last)]


def url_for(symbol: str, side: str, day: date) -> str:
    """Ruta del fichero diario de velas M1. Ojo: el mes va indexado desde cero."""
    return (
        f"/datafeed/{symbol.upper()}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}"
        f"/{side.upper()}_candles_min_1.bi5"
    )


def month_url_for(symbol: str, side: str, month: date) -> str:
    """Ruta del fichero mensual de velas H1."""
    return (
        f"/datafeed/{symbol.upper()}/{month.year:04d}/{month.month - 1:02d}"
        f"/{side.upper()}_candles_hour_1.bi5"
    )


def decode_hour_candles(payload: bytes, month: date, scale: float) -> pd.DataFrame:
    """Velas de una hora de un mes completo.

    Mismo registro de 24 bytes que las de un minuto, pero el campo de tiempo
    cuenta segundos desde el primer instante del **mes**.

    Sirve para reconstruir H4 y Diario sin bajar el minuto a minuto: los límites
    de una vela H4 caen siempre en horas en punto, así que agregar cuatro velas
    H1 da exactamente la misma vela H4 que agregar doscientos cuarenta minutos.
    Lo único que no se puede hacer con H1 es el perfil de volatilidad por minuto
    de §1.1, que exige M1.
    """
    frame = _decode(payload, pd.Timestamp(datetime(month.year, month.month, 1, tzinfo=UTC)), scale)
    if frame.empty:
        return frame

    # Comprobación barata contra el fallo silencioso de interpretar mal el campo
    # de tiempo: todo lo que salga del mes delataría un formato distinto.
    last_day = calendar.monthrange(month.year, month.month)[1]
    limit = pd.Timestamp(datetime(month.year, month.month, last_day, 23, 59, 59, tzinfo=UTC))
    if frame.index[0] < pd.Timestamp(datetime(month.year, month.month, 1, tzinfo=UTC)) or (
        frame.index[-1] > limit
    ):
        raise DukascopyError(
            f"El fichero mensual de {month:%Y-%m} produce marcas de tiempo fuera del mes "
            f"({frame.index[0]} → {frame.index[-1]}): el formato no es el esperado"
        )
    return frame


def decode_minute_candles(payload: bytes, day: date, scale: float) -> pd.DataFrame:
    """Velas de un minuto de un día; el campo de tiempo cuenta desde las 00:00 UTC."""
    return _decode(payload, pd.Timestamp(datetime(day.year, day.month, day.day, tzinfo=UTC)), scale)


def _decode(payload: bytes, origin: pd.Timestamp, scale: float) -> pd.DataFrame:
    """Descomprime y desempaqueta un `.bi5` de velas."""
    if not payload:
        return _empty_frame()

    raw = _decompress(payload)
    if len(raw) < _RECORD.size:
        return _empty_frame()
    usable = len(raw) - (len(raw) % _RECORD.size)

    values = np.frombuffer(
        raw[:usable],
        dtype=np.dtype(
            [
                ("seconds", ">i4"),
                ("open", ">i4"),
                ("close", ">i4"),
                ("low", ">i4"),
                ("high", ">i4"),
                ("volume", ">f4"),
            ]
        ),
    )
    # Dukascopy rellena SIEMPRE los 1440 minutos del día: cuando no hubo ni un
    # tick repite el último precio y pone el volumen a cero. Ese relleno no es
    # una vela plana, es ausencia de negociación, y colarlo tiene dos efectos
    # graves y silenciosos:
    #   · el hueco de fin de semana desaparece, y con él la única forma de
    #     verificar empíricamente la zona horaria del histórico (§1.1);
    #   · las velas H4 y diarias de sábado y domingo pasan a existir, con
    #     open == close, así que el módulo las cuenta como dojis y la estructura
    #     que ve el motor deja de ser la que ve el propietario en su pantalla.
    # El volumen es lo único que distingue un minuto negociado de un relleno.
    values = values[(values["volume"] > 0) & (values["open"] > 0)]
    if values.size == 0:
        return _empty_frame()

    index = origin + pd.to_timedelta(values["seconds"].astype("int64"), unit="s")
    frame = pd.DataFrame(
        {
            "open": values["open"].astype(float) / scale,
            "high": values["high"].astype(float) / scale,
            "low": values["low"].astype(float) / scale,
            "close": values["close"].astype(float) / scale,
            "volume": values["volume"].astype(float),
        },
        index=pd.DatetimeIndex(index, name="timestamp"),
    )
    return frame


def _decompress(payload: bytes) -> bytes:
    """LZMA "alone" sin marca de fin: se acepta el flujo truncado a propósito."""
    decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    try:
        return decompressor.decompress(payload)
    except lzma.LZMAError as error:
        raise DukascopyError(f"El fichero .bi5 no se pudo descomprimir: {error}") from error


def _empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        {name: pd.Series(dtype=float) for name in ("open", "high", "low", "close", "volume")}
    )
    frame.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    return frame


def _assemble(
    frames: list[pd.DataFrame], symbol: str, start: date, end: date
) -> pd.DataFrame:
    if not frames:
        raise DukascopyError(
            f"Dukascopy no devolvió ninguna barra de {symbol} entre {start} y {end}"
        )
    history = pd.concat(frames)
    history = history[~history.index.duplicated(keep="last")].sort_index()
    history.index.name = "timestamp"
    return history


def _days_between(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _months_between(start: date, end: date) -> Iterator[date]:
    month = date(start.year, start.month, 1)
    while month <= end:
        yield month
        month = (
            date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)
        )
