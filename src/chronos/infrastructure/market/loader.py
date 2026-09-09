"""Carga del histórico M1 de un par, con bid y ask separados.

Todo el proyecto dibuja y calcula sobre el mismo lado del precio. El lado
efectivo y cómo se obtuvo se declaran en el informe: si el fichero sólo trae
un OHLC, se dice que se ha asumido, no se disfraza de dato.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from chronos.application.chart.config import SymbolConfig
from chronos.domain.bars import normalize_bars, slice_bars, validate_bars
from chronos.domain.errors import DomainError

_SIDE_COLUMN = re.compile(r"^(bid|ask)[ _.\-]?(open|high|low|close|volume)$")
_OHLC = ("open", "high", "low", "close")


@dataclass(frozen=True, slots=True)
class SidedHistory:
    """Histórico M1 del lado pedido, con la traza de cómo se construyó."""

    frame: pd.DataFrame
    side: str
    #: Origen real del dato, para imprimirlo en el informe.
    provenance: str
    has_bid: bool
    has_ask: bool


def load_history(config: SymbolConfig, side: str) -> SidedHistory:
    """Devuelve el histórico M1 canónico del lado `side` (`bid`, `ask` o `mid`)."""
    if side not in ("bid", "ask", "mid"):
        raise DomainError(f"price_side desconocido: {side}")
    if not config.is_declared:
        raise DomainError(
            f"{config.symbol}: hay que indicar `path` o `bid_path` para poder dibujarlo"
        )

    bid, ask, provenance = _read_sides(config)
    if side == "bid":
        frame = bid
    elif side == "ask":
        if ask is None:
            raise DomainError(
                "price_side='ask' pero el histórico no trae lado ask. "
                "Indica `ask_path` o un fichero con columnas ask_*."
            )
        frame = ask
    else:
        if ask is None:
            raise DomainError(
                "price_side='mid' pero el histórico no trae lado ask. "
                "El punto medio necesita los dos lados."
            )
        frame = _mid(bid, ask)
        provenance += " · mid = (bid + ask) / 2 columna a columna"

    frame = slice_bars(
        frame,
        pd.Timestamp(config.start) if config.start else None,
        pd.Timestamp(config.end) if config.end else None,
    )
    validate_bars(frame)
    return SidedHistory(
        frame=frame,
        side=side,
        provenance=provenance,
        has_bid=True,
        has_ask=ask is not None,
    )


def _read_sides(
    config: SymbolConfig,
) -> tuple[pd.DataFrame, pd.DataFrame | None, str]:
    if config.bid_path:
        bid = _read_any(Path(config.bid_path), config.timezone)
        ask = _read_any(Path(config.ask_path), config.timezone) if config.ask_path else None
        origin = f"bid={config.bid_path}"
        origin += f" · ask={config.ask_path}" if config.ask_path else " · sin fichero de ask"
        return bid, ask, origin

    raw = _read_raw(Path(config.path))
    split = _split_sides(raw, config.timezone)
    if split is not None:
        bid, ask = split
        return bid, ask, f"{config.path} (columnas bid_* / ask_*)"

    frame = normalize_bars(raw, timezone=config.timezone)
    return (
        frame,
        None,
        f"{config.path} (OHLC único, se asume que es el lado declarado)",
    )


def _read_any(path: Path, timezone: str) -> pd.DataFrame:
    return normalize_bars(_read_raw(path), timezone=timezone)


def _read_raw(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise DomainError(f"No se encontró el histórico en {path}")
    if path.suffix.lower() in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if path.suffix.lower() in (".csv", ".txt"):
        return pd.read_csv(path)
    raise DomainError(f"Formato no soportado: {path.suffix or 'sin extensión'}")


def _split_sides(
    raw: pd.DataFrame, timezone: str
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Separa un fichero con columnas prefijadas en dos frames canónicos."""
    frame = raw.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]

    sides: dict[str, dict[str, str]] = {"bid": {}, "ask": {}}
    for column in frame.columns:
        match = _SIDE_COLUMN.match(column)
        if match:
            sides[match.group(1)][match.group(2)] = column

    if not all(field in sides["bid"] for field in _OHLC):
        return None
    if not all(field in sides["ask"] for field in _OHLC):
        return None

    index_columns = [c for c in frame.columns if not _SIDE_COLUMN.match(c)]
    built = []
    for side in ("bid", "ask"):
        columns = sides[side]
        subset = frame[[*index_columns, *(columns[f] for f in _OHLC)]].copy()
        subset.columns = [*index_columns, *_OHLC]
        if "volume" in columns:
            subset["volume"] = frame[columns["volume"]]
        built.append(normalize_bars(subset, timezone=timezone))
    return built[0], built[1]


def _mid(bid: pd.DataFrame, ask: pd.DataFrame) -> pd.DataFrame:
    """Punto medio columna a columna sobre las marcas de tiempo comunes.

    `high` y `low` del mid se calculan como media de los `high` y los `low` de
    cada lado, no como extremos del mid real minuto a minuto: sin datos de tick
    no hay forma de saber si el máximo del bid y el del ask ocurrieron a la vez.
    Es una aproximación, y por eso `bid` es el lado por defecto.
    """
    common = bid.index.intersection(ask.index)
    if common.empty:
        raise DomainError("Los históricos de bid y ask no comparten ninguna marca de tiempo")
    left, right = bid.loc[common], ask.loc[common]
    frame = (left[list(_OHLC)] + right[list(_OHLC)]) / 2.0
    frame["volume"] = left["volume"] if "volume" in left.columns else 0.0
    return frame
