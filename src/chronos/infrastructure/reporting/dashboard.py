"""Construcción del panel interactivo de una corrida.

Genera un único HTML autocontenido: datos, Plotly y lógica van embebidos, así
que funciona sin red y se puede archivar junto al resto de artefactos.

El panel filtra por fechas lo que ya ocurrió; no vuelve a simular. Para probar
otro periodo de verdad hay que relanzar el backtest con `--start` / `--end`.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.offline as pyo

from chronos.application.run_backtest import BacktestRun
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting import theme

ASSETS = Path(__file__).parent / "assets"

#: Por encima de este número de puntos, la curva se resume por bloques.
MAX_CURVE_POINTS = 120_000

#: Marcadores de la plantilla HTML.
_MARKER = re.compile(r"__[A-Z][A-Z_]*__")


def build_payload(
    run: BacktestRun,
    prices: pd.DataFrame | None,
    max_price_bars: int,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Serializa la corrida a la estructura que consume el panel."""
    generated_at = generated_at or SystemClock().now()
    curve, curve_downsampled = _curve_payload(run.result.equity_curve)
    price_payload, prices_downsampled = _prices_payload(prices, max_price_bars, run.spec.digits)

    return {
        "meta": {
            "symbol": run.result.symbol,
            "timeframe": run.result.timeframe.value,
            "strategy": run.result.strategy.get("name", ""),
            "params": run.result.strategy.get("params", {}),
            "initialBalance": run.result.initial_balance,
            "bars": run.result.bars_processed,
            "halted": run.result.halted_reason,
            "rejections": run.result.rejections,
            "generated": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            "curveDownsampled": curve_downsampled,
            "pricesDownsampled": prices_downsampled,
            "costs": {
                "spreadPoints": run.spec.costs.spread_points,
                "commissionPerLotPerSide": run.spec.costs.commission_per_lot_per_side,
                "slippagePoints": run.spec.costs.slippage_points,
                "swapLongPoints": run.spec.swap.long_points,
                "swapShortPoints": run.spec.swap.short_points,
            },
            "execution": asdict(run.config.execution),
            "risk": asdict(run.config.risk),
        },
        "curve": curve,
        "prices": price_payload,
        "trades": _trades_payload(run),
        "fullMetrics": run.performance.to_dict(),
    }


def render_dashboard(
    run: BacktestRun,
    prices: pd.DataFrame | None = None,
    max_price_bars: int = 20_000,
    generated_at: datetime | None = None,
) -> str:
    """Devuelve el HTML completo del panel."""
    payload = build_payload(run, prices, max_price_bars, generated_at)
    meta = payload["meta"]

    header = f"{meta['symbol']} · {meta['timeframe']} · {meta['strategy']}"
    notes = _notes(payload)

    subtitle = (
        f"{meta['bars']:,} barras · saldo inicial "
        f"{meta['initialBalance']:,.2f} USD · modo backtest"
    )
    # CSS, JS y JSON están llenos de llaves: se sustituyen marcadores en vez de
    # usar str.format, que las interpretaría como campos. La sustitución es una
    # única pasada sobre la plantilla, así que el contenido insertado (plotly.js
    # minificado incluido) nunca se reescanea en busca de más marcadores.
    replacements = {
        "__TITLE__": f"Backtest {header}",
        "__VARIABLES__": _css_variables(),
        "__CSS__": (ASSETS / "dashboard.css").read_text(encoding="utf-8"),
        "__HEADER__": header,
        "__SUBTITLE__": subtitle,
        "__NOTES__": notes,
        "__PARAMS__": _params_block(payload),
        "__GENERATED__": meta["generated"],
        "__DATA__": json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str),
        "__THEME__": json.dumps(_theme_payload(), separators=(",", ":")),
        "__PLOTLY__": pyo.get_plotlyjs(),
        "__METRICS_JS__": (ASSETS / "metrics.js").read_text(encoding="utf-8"),
        "__DASHBOARD_JS__": (ASSETS / "dashboard.js").read_text(encoding="utf-8"),
    }
    return _MARKER.sub(lambda match: replacements.get(match.group(0), match.group(0)), _TEMPLATE)


# --- Serialización ----------------------------------------------------------


def _epoch_ms(index: pd.DatetimeIndex) -> np.ndarray:
    """Marcas de tiempo en milisegundos desde epoch, en UTC."""
    utc = index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
    return utc.tz_localize(None).to_numpy(dtype="datetime64[ms]").astype("int64")


def _curve_payload(curve: pd.DataFrame) -> tuple[dict[str, list[Any]], bool]:
    if curve.empty:
        return {"times": [], "equity": [], "balance": [], "exposure": []}, False

    times = _epoch_ms(pd.DatetimeIndex(curve.index))
    equity = curve["equity"].to_numpy(dtype=float)
    balance = curve["balance"].to_numpy(dtype=float)
    exposure = curve["exposure"].to_numpy(dtype=int)

    downsampled = len(equity) > MAX_CURVE_POINTS
    if downsampled:
        keep = _representative_indices(equity, MAX_CURVE_POINTS)
        times, equity, balance, exposure = (
            np.asarray(times)[keep], equity[keep], balance[keep], exposure[keep]
        )

    return (
        {
            "times": [int(t) for t in times],
            "equity": [round(float(v), 2) for v in equity],
            "balance": [round(float(v), 2) for v in balance],
            "exposure": [int(v) for v in exposure],
        },
        downsampled,
    )


def _representative_indices(values: np.ndarray, budget: int) -> np.ndarray:
    """Reduce la serie conservando el máximo y el mínimo de cada bloque.

    Un muestreo por paso fijo se saltaría precisamente los extremos, que son los
    que determinan el drawdown. Aquí cada bloque aporta su primer punto y sus dos
    extremos, así que la forma de la curva —y el peor tramo— se preserva.
    """
    n = len(values)
    blocks = max(1, budget // 3)
    edges = np.linspace(0, n, blocks + 1, dtype=int)

    keep: list[int] = []
    for start, end in pairwise(edges):
        if end <= start:
            continue
        segment = values[start:end]
        keep.extend((start, start + int(segment.argmin()), start + int(segment.argmax())))
    keep.append(n - 1)
    return np.unique(np.asarray(keep, dtype=int))


def _prices_payload(
    prices: pd.DataFrame | None, max_bars: int, digits: int
) -> tuple[dict[str, list[Any]] | None, bool]:
    if prices is None or prices.empty:
        return None, False

    frame = prices
    downsampled = False
    if 0 < max_bars < len(frame):
        step = int(np.ceil(len(frame) / max_bars))
        frame = frame.iloc[::step]
        downsampled = True

    times = _epoch_ms(pd.DatetimeIndex(frame.index))
    return (
        {
            "times": [int(t) for t in times],
            "close": [round(float(v), digits) for v in frame["close"].to_numpy(dtype=float)],
        },
        downsampled,
    )


def _trades_payload(run: BacktestRun) -> list[dict[str, Any]]:
    digits = run.spec.digits
    return [
        {
            "id": trade.id,
            "side": trade.side.value,
            "volume": round(trade.volume, 2),
            "tIn": int(pd.Timestamp(trade.entry_time).timestamp() * 1000),
            "pIn": round(trade.entry_price, digits),
            "tOut": int(pd.Timestamp(trade.exit_time).timestamp() * 1000),
            "pOut": round(trade.exit_price, digits),
            "reason": trade.reason.value,
            "pnl": round(trade.net_pnl, 2),
            "commission": round(trade.commission, 2),
            "swap": round(trade.swap, 2),
            "r": None if trade.r_multiple is None else round(trade.r_multiple, 4),
            "mae": round(trade.mae, 2),
            "mfe": round(trade.mfe, 2),
            "bars": trade.bars_held,
            "tag": trade.tag,
        }
        for trade in run.result.trades
    ]


# --- Fragmentos -------------------------------------------------------------


def _theme_payload() -> dict[str, Any]:
    return {
        "surface": theme.SURFACE,
        "page": theme.PAGE,
        "ink": theme.INK_PRIMARY,
        "secondary": theme.INK_SECONDARY,
        "muted": theme.INK_MUTED,
        "gridline": theme.GRIDLINE,
        "baseline": theme.BASELINE,
        "series": list(theme.SERIES),
        "positive": theme.POSITIVE,
        "negative": theme.NEGATIVE,
        "diverging": theme.DIVERGING_SCALE,
        "font": theme.FONT_FAMILY,
    }


def _css_variables() -> str:
    values = {
        "--surface": theme.SURFACE,
        "--page": theme.PAGE,
        "--ink": theme.INK_PRIMARY,
        "--secondary": theme.INK_SECONDARY,
        "--muted": theme.INK_MUTED,
        "--gridline": theme.GRIDLINE,
        "--baseline": theme.BASELINE,
        "--series-1": theme.SERIES[0],
        "--positive": theme.POSITIVE,
        "--negative": theme.NEGATIVE,
        "--font": theme.FONT_FAMILY,
    }
    body = "\n  ".join(f"{name}: {value};" for name, value in values.items())
    return f":root {{\n  {body}\n}}"


def _notes(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    notes: list[str] = []
    if meta["halted"]:
        notes.append(f"<strong>Corrida detenida:</strong> {meta['halted']}")
    if meta["rejections"]:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(meta["rejections"].items()))
        notes.append(f"Señales descartadas: {detail}")
    if meta["curveDownsampled"]:
        notes.append(
            "La curva se ha resumido por bloques para aligerar el fichero "
            "(se conservan máximos y mínimos, así que el drawdown se mantiene)."
        )
    if not notes:
        return ""
    return '<div class="notice">' + "<br>".join(notes) + "</div>"


def _params_block(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    costs = meta["costs"]
    items = [f"<li><code>{key}</code>: {value}</li>" for key, value in meta["params"].items()]
    items.append(
        f"<li><code>sizing</code>: {meta['risk']['sizing']} "
        f"({meta['risk']['risk_per_trade']:.3%} por operación)</li>"
    )
    items.append(f"<li><code>fill_model</code>: {meta['execution']['fill_model']}</li>")
    items.append(
        f"<li><code>spread</code>: {costs['spreadPoints']:g} puntos · "
        f"<code>comisión</code>: {costs['commissionPerLotPerSide']:g} USD/lote/lado · "
        f"<code>slippage</code>: {costs['slippagePoints']:g} puntos</li>"
    )
    items.append(
        f"<li><code>swap</code>: {costs['swapLongPoints']:g} largos / "
        f"{costs['swapShortPoints']:g} cortos (puntos por noche y lote)</li>"
    )
    return f"<ul class='params'>{''.join(items)}</ul>"


_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__VARIABLES__
__CSS__
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>__HEADER__</h1>
    <div class="subtitle">__SUBTITLE__</div>
  </header>

  <div class="controls">
    <div class="control-group">
      <span class="control-label">Periodo</span>
      <div class="row">
        <button data-preset="all" class="active">Todo</button>
        <button data-preset="ytd">Año actual</button>
        <button data-preset="1y">1 año</button>
        <button data-preset="6m">6 meses</button>
        <button data-preset="3m">3 meses</button>
        <button data-preset="1m">1 mes</button>
      </div>
    </div>
    <div class="control-group">
      <span class="control-label">Desde / hasta</span>
      <div class="row">
        <input type="date" id="from">
        <input type="date" id="to">
      </div>
    </div>
    <div class="control-group">
      <span class="control-label">Lado</span>
      <div class="row">
        <label class="chip"><input type="checkbox" data-side="BUY" checked> Largos</label>
        <label class="chip"><input type="checkbox" data-side="SELL" checked> Cortos</label>
      </div>
    </div>
    <div class="control-group">
      <span class="control-label">Motivo de salida</span>
      <div class="row" id="reason-filters"></div>
    </div>
    <button id="reset">Restablecer</button>
  </div>

  <div class="subtitle" id="range-label"></div>
  __NOTES__

  <div class="tiles" id="tiles"></div>

  <h2>Gráficos</h2>
  <div class="panel"><div id="chart-equity"></div></div>
  <div class="panel"><div id="chart-drawdown"></div></div>
  <div class="panel"><div id="chart-price"></div></div>
  <div class="grid">
    <div class="panel"><div id="chart-monthly"></div></div>
    <div class="panel"><div id="chart-r"></div></div>
  </div>

  <div class="grid">
    <div>
      <h2>Métricas del tramo</h2>
      <div class="panel" id="metrics-table"></div>
    </div>
    <div>
      <h2>Parámetros</h2>
      <div class="panel">__PARAMS__</div>
    </div>
  </div>

  <h2>Operaciones</h2>
  <div class="panel" id="trades-table"></div>

  <footer>
    Generado por chronos-strategy · __GENERATED__ · modo backtest<br>
    Filtrar el periodo reordena lo que ya ocurrió: la estrategia tomó sus decisiones
    sobre la corrida completa. Para simular otro tramo de verdad, relanza el backtest
    con <code>--start</code> y <code>--end</code>.
  </footer>
</div>

<script type="application/json" id="chronos-data">__DATA__</script>
<script>
window.CHRONOS_DATA = JSON.parse(document.getElementById('chronos-data').textContent);
window.CHRONOS_THEME = __THEME__;
</script>
<script>__PLOTLY__</script>
<script>__METRICS_JS__</script>
<script>__DASHBOARD_JS__</script>
</body>
</html>
"""
