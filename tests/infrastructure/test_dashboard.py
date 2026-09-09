"""El panel interactivo: payload, HTML y equivalencia de métricas con Python.

El panel recalcula las métricas en el navegador al filtrar por fechas. Ese código
JavaScript es una segunda implementación de las mismas fórmulas, y una segunda
implementación sin verificar es una fuente de mentiras. Aquí se ejecuta con node
sobre una corrida real y se compara contra `compute_performance`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chronos.application.backtest.config import (
    AccountConfig,
    BacktestConfig,
    DataConfig,
    ExecutionConfig,
    ReportingConfig,
    RiskConfig,
)
from chronos.application.run_backtest import BacktestRun, run_backtest
from chronos.domain.enums import Timeframe
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.strategies.registry import create_strategy
from chronos.infrastructure.broker.simulated import build_simulated_broker
from chronos.infrastructure.data.market_data import SyntheticMarketData
from chronos.infrastructure.reporting.dashboard import (
    ASSETS,
    _representative_indices,
    build_payload,
    render_dashboard,
)

pytestmark = pytest.mark.usefixtures("spec")


@pytest.fixture
def run(spec: InstrumentSpec, tmp_path: Path) -> BacktestRun:
    config = BacktestConfig(
        account=AccountConfig(initial_balance=10_000.0),
        execution=ExecutionConfig(),
        risk=RiskConfig(sizing="fixed_fractional", risk_per_trade=0.005),
        data=DataConfig(
            source="synthetic", timeframe=Timeframe.M1, strategy_timeframe=Timeframe.M15
        ),
        reporting=ReportingConfig(output_dir=str(tmp_path)),
        strategy_name="ema_cross",
        strategy_params={"fast_period": 10, "slow_period": 30, "atr_period": 14},
    )
    market_data = SyntheticMarketData(symbol=spec.symbol, periods=60_000, seed=17)
    strategy = create_strategy("ema_cross", dict(config.strategy_params))
    return run_backtest(
        spec=spec,
        config=config,
        market_data=market_data,
        broker=build_simulated_broker(spec, config),
        strategy=strategy,
    )


# --- Payload ----------------------------------------------------------------


def test_el_payload_lleva_todo_lo_que_el_panel_necesita(run: BacktestRun) -> None:
    payload = build_payload(run, None, 20_000)

    assert payload["curve"]["times"], "la curva no puede ir vacía"
    assert len(payload["curve"]["equity"]) == len(payload["curve"]["times"])
    assert len(payload["trades"]) == len(run.result.trades)
    assert payload["meta"]["initialBalance"] == run.result.initial_balance
    assert set(payload["trades"][0]) >= {"tIn", "tOut", "pnl", "r", "side", "reason"}


def test_las_marcas_de_tiempo_son_epoch_en_milisegundos(run: BacktestRun) -> None:
    payload = build_payload(run, None, 20_000)
    primera = payload["curve"]["times"][0]
    esperada = int(run.result.equity_curve.index[0].timestamp() * 1000)
    assert primera == esperada


def test_el_resumen_de_la_curva_conserva_los_extremos() -> None:
    import numpy as np

    values = np.sin(np.linspace(0, 40, 50_000)) * 100 + 10_000
    values[12_345] = 1.0  # mínimo global escondido entre dos puntos cualesquiera

    keep = _representative_indices(values, 900)

    assert len(keep) <= 1_000
    assert 12_345 in keep, "un muestreo por paso fijo se saltaría el peor punto"
    assert values[keep].min() == values.min()
    assert values[keep].max() == values.max()


# --- HTML -------------------------------------------------------------------


def test_el_html_es_autocontenido(run: BacktestRun) -> None:
    html = render_dashboard(run, None, 20_000)

    assert "<title>Backtest XAUUSD" in html
    assert "CHRONOS_DATA" in html
    assert "ChronosMetrics" in html
    assert "Plotly" in html
    assert "src=" not in html.split("<script")[1][:200], "no debe cargar scripts externos"
    for control in ('data-preset="all"', 'id="from"', 'id="to"', 'data-side="BUY"'):
        assert control in html, f"falta el control {control}"


def test_no_quedan_marcadores_sin_sustituir(run: BacktestRun) -> None:
    import re

    from chronos.infrastructure.reporting import dashboard

    marcadores = set(re.findall(r"__[A-Z][A-Z_]*__", dashboard._TEMPLATE))
    assert marcadores, "la plantilla debería tener marcadores"

    html = render_dashboard(run, None, 20_000)
    restantes = {m for m in marcadores if m in html}
    assert not restantes, f"marcadores sin sustituir: {restantes}"


# --- Equivalencia Python / JavaScript ---------------------------------------

_RUNNER = """
const fs = require('fs');
eval(fs.readFileSync(process.argv[2], 'utf8'));
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const metrics = ChronosMetrics.compute(payload.curve, payload.trades, {
  initialBalance: payload.meta.initialBalance,
});
delete metrics.drawdownSeries;
console.log(JSON.stringify(metrics));
"""

#: Nombre en el informe de Python -> nombre en el panel.
_EQUIVALENCIAS = {
    "net_profit": "netProfit",
    "return_pct": "returnPct",
    "final_balance": "finalBalance",
    "cagr": "cagr",
    "max_drawdown": "maxDrawdown",
    "max_drawdown_pct": "maxDrawdownPct",
    "max_drawdown_duration_days": "maxDrawdownDurationDays",
    "sharpe": "sharpe",
    "sortino": "sortino",
    "calmar": "calmar",
    "ulcer_index": "ulcerIndex",
    "recovery_factor": "recoveryFactor",
    "volatility_annual": "volatilityAnnual",
    "exposure_pct": "exposurePct",
    "total_trades": "totalTrades",
    "winners": "winners",
    "losers": "losers",
    "win_rate": "winRate",
    "gross_profit": "grossProfit",
    "gross_loss": "grossLoss",
    "profit_factor": "profitFactor",
    "expectancy": "expectancy",
    "expectancy_r": "expectancyR",
    "avg_win": "avgWin",
    "avg_loss": "avgLoss",
    "payoff_ratio": "payoffRatio",
    "largest_win": "largestWin",
    "largest_loss": "largestLoss",
    "max_consecutive_wins": "maxConsecutiveWins",
    "max_consecutive_losses": "maxConsecutiveLosses",
    "avg_bars_held": "avgBarsHeld",
    "total_commission": "totalCommission",
    "total_swap": "totalSwap",
    "days": "days",
}


def test_las_metricas_del_panel_coinciden_con_las_de_python(
    run: BacktestRun, tmp_path: Path
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible: no se puede verificar el JavaScript")

    payload = build_payload(run, None, 0)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    runner = tmp_path / "runner.js"
    runner.write_text(_RUNNER, encoding="utf-8")

    output = subprocess.run(
        [node, str(runner), str(ASSETS / "metrics.js"), str(payload_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    js = json.loads(output.stdout)
    py = run.performance.to_dict()

    assert py["total_trades"] > 20, "la corrida de prueba debe tener operaciones suficientes"

    diferencias = []
    for py_key, js_key in _EQUIVALENCIAS.items():
        esperado = py[py_key]
        obtenido = js[js_key]
        if esperado is None or obtenido is None:
            if esperado != obtenido:
                diferencias.append(f"{py_key}: python={esperado} js={obtenido}")
            continue
        # El payload redondea el equity a dos decimales, así que se compara con
        # tolerancia relativa en vez de exigir identidad bit a bit.
        if abs(esperado - obtenido) > max(1e-6, abs(esperado) * 2e-4):
            diferencias.append(f"{py_key}: python={esperado!r} js={obtenido!r}")

    assert not diferencias, "métricas divergentes entre Python y el panel:\n" + "\n".join(diferencias)


# --- Ejecución real del panel -----------------------------------------------


def test_el_panel_se_dibuja_sin_errores(run: BacktestRun, tmp_path: Path) -> None:
    """Arranca `dashboard.js` contra un DOM simulado y comprueba qué produce."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible: no se puede ejecutar el JavaScript")

    from chronos.infrastructure.reporting.dashboard import _theme_payload

    payload = build_payload(run, None, 5_000)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    theme_path = tmp_path / "theme.json"
    theme_path.write_text(json.dumps(_theme_payload()), encoding="utf-8")

    stub = Path(__file__).parent / "dom_stub.js"
    output = subprocess.run(
        [
            node, str(stub), str(ASSETS / "metrics.js"), str(ASSETS / "dashboard.js"),
            str(payload_path), str(theme_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(output.stdout)

    assert not result["unknownElements"], (
        f"el panel busca elementos que la plantilla no define: {result['unknownElements']}"
    )

    dibujados = {plot["target"] for plot in result["plots"]}
    assert dibujados == {"chart-equity", "chart-drawdown", "chart-monthly", "chart-r"}, (
        "sin serie de precios embebida solo deben dibujarse cuatro gráficos"
    )
    equity = next(p for p in result["plots"] if p["target"] == "chart-equity")
    assert equity["traces"] == 2  # equity y balance
    assert equity["points"] == 2 * len(payload["curve"]["times"])

    assert "tile-value" in result["tilesHtml"]
    assert "Beneficio neto" in result["tilesHtml"]
    assert "Drawdown máximo" in result["metricsHtml"]
    assert "<table" in result["tradesHtml"]
    assert "operaciones" in result["rangeLabel"]


# --- Filtrado por fechas -----------------------------------------------------

_FILTER_RUNNER = """
const fs = require('fs');
eval(fs.readFileSync(process.argv[2], 'utf8'));
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const request = JSON.parse(process.argv[4]);

const curve = ChronosMetrics.sliceCurve(payload.curve, request.from, request.to);
const trades = ChronosMetrics.filterTrades(
  payload.trades, request.from, request.to,
  new Set(request.sides), new Set(request.reasons)
);
const base = curve.startIndex > 0
  ? payload.curve.equity[curve.startIndex - 1]
  : payload.meta.initialBalance;
const metrics = ChronosMetrics.compute(curve, trades, { initialBalance: base });
delete metrics.drawdownSeries;
console.log(JSON.stringify({
  bars: curve.times.length,
  first: curve.times[0],
  last: curve.times[curve.times.length - 1],
  startIndex: curve.startIndex,
  trades: trades.length,
  sides: Array.from(new Set(trades.map(t => t.side))),
  metrics: metrics,
}));
"""


def _filter(tmp_path: Path, payload: dict, **request: object) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible")

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    runner = tmp_path / "filter_runner.js"
    runner.write_text(_FILTER_RUNNER, encoding="utf-8")

    body = {"sides": ["BUY", "SELL"], "reasons": [], **request}
    output = subprocess.run(
        [node, str(runner), str(ASSETS / "metrics.js"), str(payload_path), json.dumps(body)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(output.stdout)


def test_el_filtro_de_fechas_recorta_curva_y_operaciones(
    run: BacktestRun, tmp_path: Path
) -> None:
    payload = build_payload(run, None, 0)
    times = payload["curve"]["times"]
    mitad = times[len(times) // 2]

    completo = _filter(tmp_path, payload, **{"from": times[0], "to": times[-1]})
    segunda = _filter(tmp_path, payload, **{"from": mitad, "to": times[-1]})

    assert completo["bars"] == len(times)
    assert segunda["first"] >= mitad
    assert segunda["last"] == times[-1]
    assert segunda["bars"] < completo["bars"]
    assert segunda["trades"] < completo["trades"]
    # Las métricas del tramo parten del equity que había al empezar, no del
    # saldo inicial de la corrida.
    assert segunda["metrics"]["initialBalance"] == pytest.approx(
        payload["curve"]["equity"][segunda["startIndex"] - 1]
    )


def test_los_tramos_no_se_solapan_ni_pierden_operaciones(
    run: BacktestRun, tmp_path: Path
) -> None:
    payload = build_payload(run, None, 0)
    times = payload["curve"]["times"]
    corte = times[len(times) // 2]

    primera = _filter(tmp_path, payload, **{"from": times[0], "to": corte - 1})
    segunda = _filter(tmp_path, payload, **{"from": corte, "to": times[-1]})

    assert primera["bars"] + segunda["bars"] == len(times)
    assert primera["trades"] + segunda["trades"] == len(payload["trades"])


def test_el_filtro_por_lado_deja_solo_ese_lado(run: BacktestRun, tmp_path: Path) -> None:
    payload = build_payload(run, None, 0)
    times = payload["curve"]["times"]

    solo_largos = _filter(
        tmp_path, payload, **{"from": times[0], "to": times[-1], "sides": ["BUY"]}
    )
    assert solo_largos["sides"] == ["BUY"]
    assert solo_largos["trades"] < len(payload["trades"])


def test_un_tramo_sin_operaciones_no_rompe_el_panel(run: BacktestRun, tmp_path: Path) -> None:
    payload = build_payload(run, None, 0)
    times = payload["curve"]["times"]

    vacio = _filter(tmp_path, payload, **{"from": times[0], "to": times[0] + 1})
    assert vacio["trades"] == 0
    assert vacio["metrics"]["totalTrades"] == 0
    assert vacio["metrics"]["expectancyR"] is None
