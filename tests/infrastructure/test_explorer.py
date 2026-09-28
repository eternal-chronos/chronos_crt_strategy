"""El explorador de velas: lo que se dibuja y lo que se marca a mano.

Es la única herramienta visual del proyecto, así que lo que se comprueba aquí
es doble:

  · que el CHASIS funciona —los cuatro pares, las temporalidades, la ventana, el
    replay, el zoom y las herramientas de mano—, y
  · que lo único calculado que dibuja es la caja de las 02:00, en H3 y en H1,
    la señal de confirmación de H1, sólo en H1, su confirmación en H4, sólo en
    H4, y las entradas, en H4, H3, H1 y M15, sin adelantarse al reloj del
    replay y separadas de lo de la mano.
    Cualquier otra traza que no sean velas es un error, y el test lo dice con
    nombre.

El JavaScript se ejecuta con node contra un DOM simulado: no sustituye a mirar
el fichero en un navegador, pero detecta lo que más se rompe —identificadores
que no existen, campos mal nombrados en el payload y excepciones dentro del
ciclo de render— y comprueba que los controles cambian la figura de verdad.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from chronos.application.chart.config import (
    DAILY,
    H1,
    H3,
    H4,
    H6,
    H12,
    M15,
    ExplorerConfig,
    ExplorerReportingConfig,
    MarksConfig,
    SymbolConfig,
)
from chronos.domain.errors import DomainError
from chronos.infrastructure.market.aggregation import aggregate
from chronos.infrastructure.market.chart_run import ChartRun, SymbolBars
from chronos.infrastructure.market.loader import SidedHistory
from chronos.infrastructure.reporting.explorer import (
    ASSETS,
    BEARISH,
    BOX_COLORS,
    BULLISH,
    ENTRY_COLORS,
    HAND_COLORS,
    SIGNAL_COLOR,
    bar_counts,
    build_payload,
    payload_size,
    render_explorer,
)
from tests.conftest import make_m1_history

#: Dos pares con precios de escalas muy distintas: es lo que obliga a que los
#: decimales, el pip y el eje sean POR PAR y no una constante del explorador.
#: El oro trae además H12, H6 y H3, que el euro no: los gráficos también son POR PAR.
GOLD = SymbolConfig(
    symbol="XAUUSD",
    label="XAUUSD (oro)",
    bid_path="x.parquet",
    decimals=2,
    timeframes=(DAILY, H12, H6, H4, H3, H1, M15),
)
EURO = SymbolConfig(symbol="EURUSD", label="EURUSD", bid_path="y.parquet", decimals=5)


def _config(**overrides: object) -> ExplorerConfig:
    base = {
        "symbols": (GOLD, EURO),
        "reporting": ExplorerReportingConfig(max_explorer_bars=0),
    }
    base.update(overrides)
    return ExplorerConfig(**base)  # type: ignore[arg-type]


def _symbol_bars(
    config: ExplorerConfig, symbol: SymbolConfig, frame: pd.DataFrame
) -> SymbolBars:
    history = SidedHistory(
        frame=frame,
        side=config.price_side,
        provenance="fixture sintética",
        has_bid=True,
        has_ask=False,
    )
    series = {
        timeframe: aggregate(frame, timeframe, config.aggregation)
        for timeframe in config.timeframes_for(symbol)
    }
    return SymbolBars(config=symbol, history=history, series=series, skipped=())


def _run(config: ExplorerConfig | None = None, unavailable: tuple[str, ...] = ()) -> ChartRun:
    config = config or _config()
    gold = make_m1_history(weeks=12)
    # El mismo camino de precios llevado a la escala de un cruce de divisas: lo
    # que cambia entre los dos pares es el orden de magnitud, que es lo que se
    # quiere probar.
    euro = gold.copy()
    for column in ("open", "high", "low", "close"):
        euro[column] = euro[column] / 2000.0 * 1.08
    return ChartRun(
        config=config,
        symbols=(
            _symbol_bars(config, GOLD, gold),
            _symbol_bars(config, EURO, euro),
        ),
        unavailable=unavailable,
    )


@pytest.fixture(scope="module")
def run() -> ChartRun:
    return _run()


# --- Payload ------------------------------------------------------------------


def test_los_pares_viajan_todos_dentro_del_mismo_payload(run: ChartRun) -> None:
    payload = build_payload(run)
    assert [item["id"] for item in payload["symbols"]] == ["XAUUSD", "EURUSD"]
    assert [item["label"] for item in payload["symbols"]] == ["XAUUSD (oro)", "EURUSD"]


def test_cada_par_lleva_sus_decimales(run: ChartRun) -> None:
    """El pip de EURUSD no es el de XAUUSD: la precisión es del par, no del explorador."""
    payload = build_payload(run)
    decimales = {item["id"]: item["decimals"] for item in payload["symbols"]}
    assert decimales == {"XAUUSD": 2, "EURUSD": 5}


def test_cada_par_lleva_sus_velas_en_sus_temporalidades(run: ChartRun) -> None:
    counts = bar_counts(build_payload(run))
    assert set(counts) == {"XAUUSD", "EURUSD"}
    assert set(counts["EURUSD"]) == {DAILY, H4, H1, M15}
    assert set(counts["XAUUSD"]) == {DAILY, H12, H6, H4, H3, H1, M15}
    for charts in counts.values():
        assert charts[M15] > charts[H1] > charts[H4] > charts[DAILY]
    oro = counts["XAUUSD"]
    assert oro[H1] > oro[H3] > oro[H4] > oro[H6] > oro[H12] > oro[DAILY]


def test_los_graficos_de_cada_par_van_de_mayor_a_menor(run: ChartRun) -> None:
    charts = {item["id"]: item["charts"] for item in build_payload(run)["symbols"]}
    assert charts["XAUUSD"] == [DAILY, H12, H6, H4, H3, H1, M15]
    assert charts["EURUSD"] == [DAILY, H4, H1, M15]


def test_la_tecla_de_h12_viaja_aunque_solo_la_use_un_par(run: ChartRun) -> None:
    assert build_payload(run)["keys"][H12] == "2"
    assert build_payload(run)["keys"][H6] == "6"
    assert build_payload(run)["keys"][H3] == "3"


def test_una_temporalidad_de_par_no_soportada_falla_con_nombre() -> None:
    raro = replace(GOLD, timeframes=(DAILY, "H8"))
    with pytest.raises(DomainError, match="H8"):
        _config(symbols=(raro, EURO))


def test_la_duracion_de_la_vela_se_mide_sobre_las_velas(run: ChartRun) -> None:
    """El replay la necesita para saber cuándo cerró cada una."""
    spans = build_payload(run)["symbols"][0]["spans"]
    assert spans[M15] == 15
    assert spans[H1] == 60
    assert spans[H3] == 180
    assert spans[H4] == 240
    assert spans[H6] == 360
    assert spans[H12] == 720
    # Con ancla de sesión el diario no dura siempre lo mismo, pero la moda sí.
    assert spans[DAILY] == 1440


def test_las_capas_calculadas_del_payload_son_la_caja_y_la_senal(run: ChartRun) -> None:
    """Velas, cajas de las 02:00 de H3 y señales de H1 y de H4, y nada más.

    Si un día aparece una clave que no sea de las de abajo, será porque alguien
    ha metido otra capa calculada y este test tiene que enterarse.
    """
    payload = build_payload(run)
    assert set(payload) == {
        "meta", "colors", "labels", "keys", "marks", "symbols", "unavailable"
    }
    for symbol in payload["symbols"]:
        assert set(symbol) == {
            "id", "label", "decimals", "side", "provenance",
            "charts", "spans", "bars", "skipped", "boxes", "signals", "h4signals", "entries",
        }
        if H3 not in symbol["charts"]:
            assert symbol["boxes"] == [], f"{symbol['id']}: sin H3 no hay cajas"
            assert symbol["signals"] == [], f"{symbol['id']}: sin H3 no hay señales"
            assert symbol["h4signals"] == [], f"{symbol['id']}: sin H3 no hay señales de H4"
            continue
        assert symbol["h4signals"], f"{symbol['id']}: la fixture debería dar alguna de H4"
        assert any(e["exitAt"] is not None for e in symbol["entries"]), (
            f"{symbol['id']}: la fixture debería dar alguna entrada cerrada"
        )
        de_h4 = {(s["known"], s["dir"]) for s in symbol["h4signals"]}
        for entrada in symbol["entries"]:
            assert set(entrada) == {
                "dir", "at", "entry", "stop", "take", "exitAt", "exit", "reason", "r"
            }
            vende = entrada["dir"] == "bearish"
            assert (entrada["take"] < entrada["entry"] < entrada["stop"]) == vende
            assert (entrada["stop"] < entrada["entry"] < entrada["take"]) != vende
            # Cada entrada, al cierre de la vela de 15M que abre al confirmar H4.
            quince = symbol["spans"][M15]
            assert any(
                known + symbol["spans"][H1] + quince == entrada["at"] and d == entrada["dir"]
                for known, d in de_h4
            )
            if entrada["exitAt"] is None:
                assert entrada["exit"] is None and entrada["r"] is None
                continue
            assert entrada["at"] < entrada["exitAt"]
            assert entrada["reason"] in {"stop", "take", "cierre_16:30"}
            if entrada["reason"] == "stop":
                assert (entrada["exit"], entrada["r"]) == (entrada["stop"], -1.0)
            if entrada["reason"] == "take":
                assert entrada["exit"] == entrada["take"] and entrada["r"] > 0
        de_h1 = {(s["t"], s["dir"]) for s in symbol["signals"]}
        for senal in symbol["h4signals"]:
            assert set(senal) == {"dir", "from", "t", "until", "known", "swept", "opposite"}
            assert senal["from"] < senal["t"] <= senal["known"] < senal["until"]
            assert (senal["swept"] < senal["opposite"]) == (senal["dir"] == "bullish")
            # Toda confirmación de H4 lo es de una señal de H1 del mismo lado.
            assert any(t <= senal["known"] and d == senal["dir"] for t, d in de_h1)
        assert symbol["signals"], f"{symbol['id']}: la fixture debería dar alguna señal"
        for senal in symbol["signals"]:
            assert set(senal) == {"dir", "from", "t", "until", "swept", "opposite"}
            assert senal["dir"] in {"bullish", "bearish"}
            assert senal["from"] < senal["t"] < senal["until"]
            barrido_abajo = senal["swept"] < senal["opposite"]
            assert barrido_abajo == (senal["dir"] == "bullish")
        assert symbol["boxes"], f"{symbol['id']}: la fixture debería dar alguna caja"
        tipos = {caja["kind"] for caja in symbol["boxes"]}
        assert tipos == {"rango", "vela_previa", "ruptura", "barrido", "fin_rango"}, (
            "la fixture debería dar los cuatro tipos"
        )
        for caja in symbol["boxes"]:
            assert set(caja) == {"kind", "dir", "ref", "known", "until", "high", "low"}
            assert (caja["dir"] in {"bullish", "bearish"}) == (caja["kind"] == "rango")
            assert caja["ref"] <= caja["known"] < caja["until"]
            assert caja["low"] <= caja["high"]


def test_las_cajas_se_calculan_con_todo_h3_aunque_se_recorte(run: ChartRun) -> None:
    """Cuántas velas se embeben no puede cambiar qué cajas hay."""
    corto = _config(reporting=ExplorerReportingConfig(max_explorer_bars=5))
    completo = build_payload(run)["symbols"][0]["boxes"]
    recortado = build_payload(replace(run, config=corto))["symbols"][0]["boxes"]
    assert recortado == completo
    senales = build_payload(run)["symbols"][0]["signals"]
    assert build_payload(replace(run, config=corto))["symbols"][0]["signals"] == senales
    de_h4 = build_payload(run)["symbols"][0]["h4signals"]
    assert build_payload(replace(run, config=corto))["symbols"][0]["h4signals"] == de_h4
    entradas = build_payload(run)["symbols"][0]["entries"]
    assert build_payload(replace(run, config=corto))["symbols"][0]["entries"] == entradas


def test_el_color_de_la_senal_no_es_de_velas_ni_de_cajas_ni_de_la_mano() -> None:
    assert SIGNAL_COLOR not in {BULLISH, BEARISH, *HAND_COLORS, *BOX_COLORS.values()}


def test_los_colores_de_las_cajas_no_son_ni_de_velas_ni_de_la_mano() -> None:
    colores = set(BOX_COLORS.values())
    assert len(colores) == 3
    assert not colores & {BULLISH, BEARISH, *HAND_COLORS}


def test_los_nombres_de_las_marcas_salen_de_la_configuracion() -> None:
    """Renombrarlas no puede exigir tocar el JavaScript."""
    marks = MarksConfig(rects=("CRT alto", "CRT bajo"), lines=("Objetivo",))
    payload = build_payload(_run(_config(marks=marks)))
    assert payload["marks"] == {"rects": ["CRT alto", "CRT bajo"], "lines": ["Objetivo"]}
    assert payload["colors"]["rects"] == {
        "CRT alto": HAND_COLORS[0],
        "CRT bajo": HAND_COLORS[1],
    }
    assert payload["colors"]["lines"] == {"Objetivo": HAND_COLORS[0]}


def test_no_se_admiten_mas_marcas_que_colores_de_mano() -> None:
    """Un cuarto nombre tendría que repetir un color y dejaría de distinguirse."""
    with pytest.raises(DomainError, match="tres nombres como mucho"):
        MarksConfig(rects=("a", "b", "c", "d"))


def test_los_colores_de_la_mano_no_son_los_de_las_velas() -> None:
    """El día que haya capas calculadas, lo de la mano tiene que seguir separándose."""
    assert BULLISH not in HAND_COLORS
    assert BEARISH not in HAND_COLORS
    assert len(set(HAND_COLORS)) == len(HAND_COLORS)


def test_los_pares_que_no_se_pudieron_cargar_se_declaran() -> None:
    """Una pestaña que falta se lee como que ese par no existe."""
    payload = build_payload(_run(unavailable=("GBPUSD: sin histórico declarado",)))
    assert payload["unavailable"] == ["GBPUSD: sin histórico declarado"]


def test_max_explorer_bars_recorta_y_lo_dice(run: ChartRun) -> None:
    corto = _config(reporting=ExplorerReportingConfig(max_explorer_bars=50))
    payload = build_payload(replace(run, config=corto))
    m15 = payload["symbols"][0]["bars"][M15]
    assert m15["truncated"] is True
    assert len(m15["t"]) == 50
    assert m15["total"] > 50


def test_sin_ningun_par_cargable_la_corrida_no_existe() -> None:
    with pytest.raises(DomainError, match="Ningún par"):
        ChartRun(config=_config(), symbols=(), unavailable=("XAUUSD: sin fichero",))


# --- HTML ---------------------------------------------------------------------


def test_el_html_es_autocontenido(run: ChartRun) -> None:
    html = render_explorer(run)
    assert "__DATA__" not in html
    assert "__PLOTLY__" not in html
    assert "__EXPLORER_JS__" not in html
    assert '<script id="explorer-data"' in html
    # Sin ninguna llamada a la red: se abre con doble clic y funciona sin conexión.
    assert "src=" not in html.split('<script id="explorer-data"')[0]


def test_el_html_dice_en_la_cabecera_que_hay_entradas_calculadas(run: ChartRun) -> None:
    html = render_explorer(run)
    assert "las entradas calculadas" in html
    assert "XAUUSD (oro) · EURUSD" in html


def test_el_json_embebido_no_puede_cerrar_la_etiqueta(run: ChartRun) -> None:
    html = render_explorer(run)
    datos = html.split('<script id="explorer-data" type="application/json">')[1]
    datos = datos.split("</script>")[0]
    assert "</" not in datos


def test_el_tamano_del_payload_se_puede_vigilar(run: ChartRun) -> None:
    assert payload_size(build_payload(run)) > 0


# --- El JavaScript, contra un DOM simulado ------------------------------------


def _draw(run: ChartRun, tmp_path: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible: no se puede ejecutar el JavaScript")

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(build_payload(run), default=str), encoding="utf-8")
    stub = Path(__file__).parent / "explorer_dom_stub.js"
    output = subprocess.run(
        [node, str(stub), str(ASSETS / "explorer.js"), str(payload_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(output.stdout)


@pytest.fixture(scope="module")
def drawn(run: ChartRun, tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _draw(run, tmp_path_factory.mktemp("explorer"))


def _step(resultado: dict, label: str) -> dict:
    return next(step for step in resultado["steps"] if step["label"] == label)


def test_el_explorador_se_dibuja_sin_errores(drawn: dict) -> None:
    assert not drawn["unknownElements"], (
        f"el explorador busca elementos que la plantilla no define: {drawn['unknownElements']}"
    )
    assert drawn["chartTabs"] == ["Diario", "H12", "H6", "H4", "H3", "H1", "M15"]
    assert drawn["presetLabels"][0] == "Todo"
    assert _step(drawn, "todo")["plot"]["target"] == "chart"


CAJAS = {
    "Caja de las 02:00 · rango bajista",
    "Caja de las 02:00 · rango alcista",
    "Caja de las 02:00 · vela",
}


SENALES = {
    "Señal H1 · línea del turtle soup",
    "Señal H1 · otro extremo",
    "Señal H1 · confirmación",
}
SENALES_H4 = {
    "Señal H4 · línea del turtle soup",
    "Señal H4 · otro extremo",
    "Señal H4 · buscar entradas",
}


ENTRADAS = {"Entrada · tramo del take", "Entrada · tramo del stop", "Entrada · salida"}


def test_lo_calculado_es_la_caja_en_h3_y_h1_y_cada_senal_en_la_suya(drawn: dict) -> None:
    """Comprobado en cada paso del recorrido: en H3 cajas, en H1 cajas y
    señales de H1, en H4 señales de H4, en esas tres y en M15 las entradas, y
    en el resto sólo velas."""
    permitidas = {
        H3: CAJAS | ENTRADAS,
        H1: CAJAS | SENALES | ENTRADAS,
        H4: SENALES_H4 | ENTRADAS,
        M15: ENTRADAS,
    }
    for step in drawn["steps"]:
        calculadas = set(step["plot"]["calculated"])
        propias = permitidas.get(step["chart"], set())
        assert calculadas <= propias, (
            f"en «{step['label']}» ({step['chart']}) hay trazas calculadas que no tocan: "
            f"{calculadas - propias}"
        )


def _minuto(texto: str) -> int:
    return int((pd.Timestamp(texto, tz="UTC") - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds() // 60)


def _cajas_esperadas(symbol: dict, step: dict) -> set[tuple[int, int, float, float]]:
    """La caja actual —la última que se sabe al cierre de la última vela a la
    vista—, como (desde, hasta, máximo, mínimo), si cae en el tramo."""
    paso = symbol["spans"][H3]
    inicio = _minuto(step["plot"]["firstBar"])
    borde = _minuto(step["plot"]["lastBar"]) + symbol["spans"][step["chart"]]
    sabidas = [caja for caja in symbol["boxes"] if caja["known"] + paso <= borde]
    if not sabidas:
        return set()
    caja = sabidas[-1]
    desde, hasta = max(caja["ref"], inicio), min(caja["until"], borde)
    return {(desde, hasta, caja["high"], caja["low"])} if hasta > desde else set()


def _cajas_dibujadas(step: dict) -> set[tuple[int, int, float, float]]:
    dibujadas = set()
    for traza in step["plot"]["boxes"]:
        for n in range(traza["count"]):
            x, y = traza["x"][n * 6 : n * 6 + 5], traza["y"][n * 6 : n * 6 + 5]
            dibujadas.add((_minuto(x[0]), _minuto(x[1]), y[0], y[2]))
    return dibujadas


def test_en_h3_y_h1_se_dibujan_las_cajas_que_se_saben(run: ChartRun, drawn: dict) -> None:
    """Fuera y dentro del replay, sólo la actual: las de días anteriores y la
    sustituida por la vela de las 02:00 no se dibujan."""
    simbolos = {symbol["id"]: symbol for symbol in build_payload(run)["symbols"]}
    vistos = {H3: 0, H1: 0}
    for step in drawn["steps"]:
        if step["chart"] not in vistos or "oculta hasta Revelar" in step["notes"]:
            continue
        esperadas = _cajas_esperadas(simbolos[step["symbol"]], step)
        assert _cajas_dibujadas(step) == esperadas, f"«{step['label']}»"
        assert sum(traza["count"] for traza in step["plot"]["boxes"]) <= 1, f"«{step['label']}»"
        vistos[step["chart"]] += len(esperadas)
    assert all(vistos.values()), f"algún paso debería enseñar cajas en H3 y en H1: {vistos}"


def _senales_esperadas(symbol: dict, step: dict) -> set[tuple[int, int, int, float, float]]:
    """Las señales que se saben al borde del tramo y caen en él, como (vela,
    desde, hasta, extremo barrido, otro extremo), con las líneas recortadas.
    Las de H1 se saben al cierre de su vela; las de H4, al cierre de la vela de
    H1 que las da, y en replay su borde es el reloj."""
    en_h4 = step["chart"] == H4
    hora = symbol["spans"][H1]
    inicio = _minuto(step["plot"]["firstBar"])
    borde = _minuto(step["plot"]["lastBar"]) + symbol["spans"][step["chart"]]
    if en_h4 and "REPLAY" in step["notes"]:
        borde = _minuto(_reloj(step))
    return {
        (s["t"], max(s["from"], inicio), min(s["until"], borde), s["swept"], s["opposite"])
        for s in symbol["h4signals" if en_h4 else "signals"]
        if (s["known"] if en_h4 else s["t"]) + hora <= borde and s["t"] >= inicio
    }


def _senales_dibujadas(step: dict) -> set[tuple[int, int, int, float, float]]:
    trazas = {traza["name"]: traza for traza in step["plot"]["signals"]}
    if not trazas:
        return set()
    capa = "H4" if step["chart"] == H4 else "H1"
    barrida = trazas[f"Señal {capa} · línea del turtle soup"]
    otra = trazas[f"Señal {capa} · otro extremo"]
    marcas = trazas[f"Señal {capa} · confirmación" if capa == "H1" else "Señal H4 · buscar entradas"]
    return {
        (
            _minuto(marcas["x"][n]),
            _minuto(barrida["x"][n * 3]),
            _minuto(barrida["x"][n * 3 + 1]),
            barrida["y"][n * 3],
            otra["y"][n * 3],
        )
        for n in range(len(marcas["x"]))
    }


def test_en_h1_se_dibujan_las_senales_que_se_saben(run: ChartRun, drawn: dict) -> None:
    simbolos = {symbol["id"]: symbol for symbol in build_payload(run)["symbols"]}
    vistas = 0
    for step in drawn["steps"]:
        if step["chart"] != H1 or "oculta hasta Revelar" in step["notes"]:
            continue
        esperadas = _senales_esperadas(simbolos[step["symbol"]], step)
        assert _senales_dibujadas(step) == esperadas, f"«{step['label']}»"
        vistas += len(esperadas)
    assert vistas, "algún paso del recorrido debería enseñar señales en H1"
    todo = _step(drawn, "h1-todo")
    assert len(_senales_dibujadas(todo)) == len(simbolos[todo["symbol"]]["signals"])


def test_en_h4_se_dibujan_las_senales_que_se_saben(run: ChartRun, drawn: dict) -> None:
    simbolos = {symbol["id"]: symbol for symbol in build_payload(run)["symbols"]}
    vistas = 0
    for step in drawn["steps"]:
        if step["chart"] != H4 or "oculta hasta Revelar" in step["notes"]:
            continue
        esperadas = _senales_esperadas(simbolos[step["symbol"]], step)
        assert _senales_dibujadas(step) == esperadas, f"«{step['label']}»"
        vistas += len(esperadas)
    assert vistas, "algún paso del recorrido debería enseñar señales en H4"
    todo = _step(drawn, "h4-todo")
    assert len(_senales_dibujadas(todo)) == len(simbolos[todo["symbol"]]["h4signals"])


def test_las_senales_de_h4_van_en_violeta_y_dicen_que_son_para_buscar_entradas(
    drawn: dict,
) -> None:
    trazas = {traza["name"]: traza for traza in _step(drawn, "h4-todo")["plot"]["signals"]}
    assert set(trazas) == SENALES_H4
    for traza in trazas.values():
        assert traza["color"] == SIGNAL_COLOR
        for texto in traza["captions"]:
            assert texto.startswith("Señal de confirmación H4 · buscar entradas (calculada)")
    assert trazas["Señal H4 · línea del turtle soup"]["dash"] == "solid"
    assert trazas["Señal H4 · otro extremo"]["dash"] == "dash"


def test_las_senales_van_en_violeta_con_su_trazo_y_su_triangulo(drawn: dict) -> None:
    trazas = {traza["name"]: traza for traza in _step(drawn, "h1-todo")["plot"]["signals"]}
    assert set(trazas) == SENALES
    for traza in trazas.values():
        assert traza["color"] == SIGNAL_COLOR
        for texto in traza["captions"]:
            assert texto.startswith("Señal de confirmación H1 (calculada)")
            assert "turtle soup" in texto
    assert trazas["Señal H1 · línea del turtle soup"]["dash"] == "solid"
    assert trazas["Señal H1 · otro extremo"]["dash"] == "dash"
    assert set(trazas["Señal H1 · confirmación"]["symbols"]) <= {"triangle-up", "triangle-down"}


def test_el_replay_no_ensena_senales_mas_alla_del_reloj(drawn: dict) -> None:
    step = _step(drawn, "replay-en-h1")
    assert step["chart"] == H1
    for traza in step["plot"]["signals"] + step["plot"]["boxes"]:
        assert traza["maxX"][:16] <= _reloj(step)
    for label in ("replay-inicio", "replay-paso", "replay-en-h4"):
        step = _step(drawn, label)
        assert step["chart"] == H4
        for traza in step["plot"]["signals"]:
            assert traza["maxX"][:16] <= _reloj(step)


def test_las_cajas_van_rellenas_con_su_color_y_su_origen(drawn: dict) -> None:
    trazas = [t for step in drawn["steps"] for t in step["plot"]["boxes"]]
    assert trazas
    grupos = {"rango bajista": "bearish", "rango alcista": "bullish", "vela": "candle"}
    for traza in trazas:
        assert traza["color"] == BOX_COLORS[grupos[traza["name"].split(" · ")[1]]]
        assert traza["fill"] == "toself"
        for texto in traza["captions"]:
            assert texto.startswith("Caja de las 02:00 (calculada)")
            if traza["name"].endswith("vela"):
                assert any(origen in texto for origen in (
                    "la vela anterior", "cerró fuera de la caja", "tocó el segundo extremo",
                    "terminó el rango",
                ))
            else:
                assert "vivo antes de las 02:00 NY" in texto


def test_el_replay_no_ensena_cajas_mas_alla_del_reloj(drawn: dict) -> None:
    """Ni una caja antes de cerrar su vela, ni estirada más allá del reloj."""
    step = _step(drawn, "replay-en-h3")
    assert step["chart"] == H3
    assert step["plot"]["boxes"], "el replay en H3 debería enseñar alguna caja"
    for traza in step["plot"]["boxes"]:
        assert traza["maxX"][:16] <= _reloj(step)


def test_la_auditoria_ciega_oculta_las_cajas_hasta_revelar(drawn: dict) -> None:
    ciega = _step(drawn, "ciega")
    assert ciega["chart"] == H3
    assert ciega["plot"]["calculated"] == []
    assert "oculta hasta Revelar" in ciega["notes"]
    revelada = _step(drawn, "ciega-revelada")
    assert "oculta hasta Revelar" not in revelada["notes"]
    assert revelada["plot"]["calculated"]


def test_el_estado_dice_que_cajas_se_ven(drawn: dict) -> None:
    assert "caja de las 02:00 NY de H3 (calculada por el motor)" in _step(drawn, "grafico-H3")["notes"]
    assert "caja de las 02:00 NY de H3 (calculada por el motor)" in _step(drawn, "grafico-H1")["notes"]
    assert "sólo se dibuja en H3 y en H1" in _step(drawn, "todo")["notes"]
    assert "señal de confirmación en H1 (calculada por el motor)" in _step(drawn, "grafico-H1")["notes"]
    assert "la de H1 sólo se dibuja en H1 y la de H4 sólo en H4" in _step(drawn, "grafico-H3")["notes"]
    assert "señal de confirmación en H4 para buscar entradas (calculada por el motor)" in (
        _step(drawn, "grafico-H4")["notes"]
    )


def test_lo_unico_que_hay_en_shapes_lo_ha_puesto_una_mano(drawn: dict) -> None:
    for step in drawn["steps"]:
        assert step["plot"]["shapes"] == 0, (
            f"en «{step['label']}» hay formas que no ha dibujado el propietario"
        )


def test_el_estado_dice_que_entradas_se_ven_y_el_capital(drawn: dict) -> None:
    """Un gráfico pelado sin decirlo se lee como que ahí no pasó nada."""
    notas = _step(drawn, "todo")["notes"]
    assert "entradas (calculadas): sólo se dibujan en H4, H3, H1 y M15" in notas
    assert "lo pone tu mano" in notas
    for grafico in (H4, H3, H1, M15):
        assert "ENTRADAS (calculadas por el motor)" in _step(drawn, f"grafico-{grafico}")["notes"]
    assert "CAPITAL DE LA ESTRATEGIA: Capital 50,00 $ · arranca con el replay" in notas


# --- Entradas y capital de la estrategia --------------------------------------


def _entradas_esperadas(symbol: dict, step: dict) -> set[tuple[int, int, float, float, float]]:
    """Las operaciones que se saben al borde del tramo y caen en él, como (desde,
    hasta, entrada, take, stop). Fuera del replay el borde es el cierre de la
    última vela a la vista; en replay, el reloj. La que no ha salido al borde
    se estira hasta él."""
    inicio = _minuto(step["plot"]["firstBar"])
    borde = _minuto(step["plot"]["lastBar"]) + symbol["spans"][step["chart"]]
    if "REPLAY" in step["notes"]:
        borde = _minuto(_reloj(step))
    esperadas = set()
    for e in symbol["entries"]:
        cerrada = e["exitAt"] is not None and e["exitAt"] <= borde
        hasta = e["exitAt"] if cerrada else borde
        if e["at"] <= borde and hasta >= inicio:
            esperadas.add((max(e["at"], inicio), hasta, e["entry"], e["take"], e["stop"]))
    return esperadas


def _entradas_dibujadas(step: dict) -> set[tuple[int, int, float, float, float]]:
    trazas = {traza["name"]: traza for traza in step["plot"]["entries"]}
    if not trazas:
        return set()
    take, stop = trazas["Entrada · tramo del take"], trazas["Entrada · tramo del stop"]
    return {
        (
            _minuto(take["x"][n]),
            _minuto(take["x"][n + 1]),
            take["y"][n + 2],
            take["y"][n],
            stop["y"][n],
        )
        for n in range(0, len(take["x"]), 6)
    }


def test_las_entradas_se_dibujan_desde_que_entran_hasta_que_salen(
    run: ChartRun, drawn: dict
) -> None:
    simbolos = {symbol["id"]: symbol for symbol in build_payload(run)["symbols"]}
    vistas = 0
    for step in drawn["steps"]:
        if step["chart"] not in {H4, H3, H1, M15} or "ocultas hasta Revelar" in step["notes"]:
            continue
        esperadas = _entradas_esperadas(simbolos[step["symbol"]], step)
        assert _entradas_dibujadas(step) == esperadas, f"«{step['label']}»"
        vistas += len(esperadas)
    assert vistas, "algún paso del recorrido debería enseñar entradas"
    assert _entradas_dibujadas(_step(drawn, "capital-en-m15")), "en M15 se debería ver la entrada"


def test_las_entradas_van_con_sus_colores_y_dicen_lo_que_son(drawn: dict) -> None:
    trazas = [t for step in drawn["steps"] for t in step["plot"]["entries"]]
    assert {t["name"] for t in trazas} == ENTRADAS
    for traza in trazas:
        if traza["name"] == "Entrada · salida":
            assert set(traza["color"]) <= set(ENTRY_COLORS.values())
        else:
            nivel = "take" if traza["name"].endswith("take") else "stop"
            assert (traza["color"], traza["fill"]) == (ENTRY_COLORS[nivel], "toself")
        for texto in traza["captions"]:
            assert texto.startswith("Entrada (calculada) · ")
            assert "extremo del turtle soup" in texto and "objetivo del rango de H3" in texto
    assert set(ENTRY_COLORS.values()).isdisjoint(
        {BULLISH, BEARISH, SIGNAL_COLOR, *BOX_COLORS.values(), *HAND_COLORS}
    )


def test_el_replay_no_ensena_entradas_ni_salidas_mas_alla_del_reloj(drawn: dict) -> None:
    for label in ("capital-en-la-entrada", "capital-en-m15", "capital-tras-la-salida"):
        step = _step(drawn, label)
        assert step["plot"]["entries"], f"«{label}» debería enseñar alguna entrada"
        for traza in step["plot"]["entries"]:
            assert traza["maxX"][:16] <= _reloj(step), f"«{label}»"


def _capital(entradas: list[dict], origen: int, reloj: int) -> tuple[float, int, int]:
    """El capital a mano: 50 $, el 10 % del capital de cada momento en cada
    entrada posterior al arranque, y su R al salir. Devuelve (capital,
    cerradas, abiertas)."""
    dentro = [e for e in entradas if origen < e["at"] <= reloj]
    eventos = sorted(
        [(e["at"], 1, n) for n, e in enumerate(dentro)]
        + [(e["exitAt"], 0, n) for n, e in enumerate(dentro)
           if e["exitAt"] is not None and e["exitAt"] <= reloj]
    )
    capital, riesgo, cerradas = 50.0, {}, 0
    for _, entra, n in eventos:
        if entra:
            riesgo[n] = round(capital * 0.10, 2)
        else:
            capital = round(capital + round(riesgo[n] * dentro[n]["r"], 2), 2)
            cerradas += 1
    return capital, cerradas, len(dentro) - cerradas


def _dinero(valor: float) -> str:
    entero, decimales = f"{valor:.2f}".split(".")
    return f"{int(entero):,}".replace(",", ".") + "," + decimales + " $"


def test_el_capital_arranca_en_50_y_suma_cada_entrada_con_el_10_por_ciento(
    run: ChartRun, drawn: dict
) -> None:
    entradas = build_payload(run)["symbols"][0]["entries"]
    inicio = _step(drawn, "capital-inicio")
    assert inicio["strategyCapital"].startswith("Capital 50,00 $ · 0 cerradas")
    origen = _minuto(_reloj(inicio))
    for label in ("capital-en-la-entrada", "capital-tras-la-salida"):
        step = _step(drawn, label)
        capital, cerradas, abiertas = _capital(entradas, origen, _minuto(_reloj(step)))
        assert step["strategyCapital"].startswith(f"Capital {_dinero(capital)} · "), label
        assert f"{cerradas} cerrada" in step["strategyCapital"], label
        assert f"la siguiente arriesga {_dinero(round(capital * 0.10, 2))}" in (
            step["strategyCapital"]
        ), label
        if abiertas:
            assert "abierta" in step["strategyCapital"], label
    tras = _step(drawn, "capital-tras-la-salida")
    assert not tras["strategyCapital"].startswith("Capital 50,00 $"), (
        "tras la primera salida el capital se debería haber movido"
    )
    assert _step(drawn, "capital-fuera")["strategyCapital"] == (
        "Capital 50,00 $ · arranca con el replay"
    )


# --- El selector de par --------------------------------------------------------


def test_hay_un_boton_por_par_con_su_nombre(drawn: dict) -> None:
    assert drawn["symbolIds"] == ["XAUUSD", "EURUSD"]
    assert drawn["symbolLabels"] == ["XAUUSD (oro)", "EURUSD"]


def test_cambiar_de_par_cambia_las_velas_y_el_eje(drawn: dict) -> None:
    oro = _step(drawn, "par-XAUUSD")
    euro = _step(drawn, "par-EURUSD")
    assert oro["symbol"] == "XAUUSD"
    assert euro["symbol"] == "EURUSD"
    # El eje de precios lleva los decimales del par que se está mirando.
    assert oro["plot"]["yTickFormat"] == ".2f"
    assert euro["plot"]["yTickFormat"] == ".5f"
    # Y el globo de la vela dice de qué par es.
    assert "XAUUSD (oro)" in oro["plot"]["hover"]
    assert "EURUSD" in euro["plot"]["hover"]


def test_cambiar_de_par_no_mueve_la_ventana_de_fechas(drawn: dict) -> None:
    oro = _step(drawn, "par-XAUUSD")
    euro = _step(drawn, "par-EURUSD")
    assert (oro["from"], oro["to"]) == (euro["from"], euro["to"])


def test_las_flechas_verticales_cambian_de_par(drawn: dict) -> None:
    assert _step(drawn, "par-teclado-abajo")["symbol"] == "EURUSD"
    assert _step(drawn, "par-teclado-arriba")["symbol"] == "XAUUSD"
    # Salvo con el foco en un campo de texto: ahí las teclas escriben.
    assert _step(drawn, "par-teclado-en-un-campo")["symbol"] == "XAUUSD"


def test_el_boton_del_par_dice_lo_que_lleva(drawn: dict) -> None:
    titulo = drawn["symbolTitles"][0]
    assert "lado bid" in titulo
    assert "2 decimales" in titulo


# --- Temporalidades, ventana y vista -------------------------------------------


def test_cada_temporalidad_dibuja_sus_velas(drawn: dict) -> None:
    velas = {
        timeframe: _step(drawn, f"grafico-{timeframe}")["plot"]["bars"]
        for timeframe in (DAILY, H12, H6, H4, H3, H1, M15)
    }
    assert velas[M15] > velas[H1] > velas[H3] > velas[H4] > velas[H6] > velas[H12] > velas[DAILY]


def test_las_teclas_saltan_de_grafico(drawn: dict) -> None:
    assert _step(drawn, f"teclado-tf-{H4}")["chart"] == H4
    assert _step(drawn, f"teclado-tf-{H12}")["chart"] == H12
    assert _step(drawn, f"teclado-tf-{H6}")["chart"] == H6
    assert _step(drawn, f"teclado-tf-{H3}")["chart"] == H3
    assert _step(drawn, f"teclado-tf-{M15}")["chart"] == M15
    # Con el foco en un campo la tecla escribe y no salta.
    assert _step(drawn, "teclado-tf-en-un-campo")["chart"] == M15


def test_el_par_sin_h12_cae_a_su_primer_grafico(drawn: dict) -> None:
    assert _step(drawn, f"grafico-propio-{H12}")["chart"] == H12
    otro = _step(drawn, "grafico-propio-otro-par")
    assert otro["symbol"] == "EURUSD"
    assert otro["chart"] == DAILY
    assert otro["plot"]["bars"] > 0


def test_la_ventana_avanza_y_retrocede_sin_solapar(drawn: dict) -> None:
    corto = _step(drawn, "preset-corto")
    anterior = _step(drawn, "ventana-anterior")
    siguiente = _step(drawn, "ventana-siguiente")
    assert anterior["to"] < corto["from"]
    assert (siguiente["from"], siguiente["to"]) == (corto["from"], corto["to"])


def test_las_flechas_mueven_la_ventana(drawn: dict) -> None:
    izquierda = _step(drawn, "teclado-izquierda")
    derecha = _step(drawn, "teclado-derecha")
    en_campo = _step(drawn, "teclado-en-un-campo")
    assert izquierda["to"] < derecha["to"]
    assert (en_campo["from"], en_campo["to"]) == (derecha["from"], derecha["to"])


def test_la_vista_de_lineas_dibuja_una_sola_traza(drawn: dict) -> None:
    lineas = _step(drawn, "lineas")["plot"]
    velas = _step(drawn, "velas")["plot"]
    precio = [trace for trace in lineas["traces"] if trace["name"] not in CAJAS]
    assert [trace["type"] for trace in precio] == ["scatter"]
    assert velas["traces"][0]["type"] == "candlestick"


# --- Replay ---------------------------------------------------------------------


def test_el_replay_no_dibuja_mas_alla_del_reloj(drawn: dict) -> None:
    for label in ("replay-inicio", "replay-paso", "replay-con-zoom"):
        step = _step(drawn, label)
        assert step["plot"]["maxX"] is not None
        assert "REPLAY" in step["notes"]


def test_el_replay_apaga_los_controles_de_periodo(drawn: dict) -> None:
    assert _step(drawn, "replay-paso")["replayLocked"] is True
    assert _step(drawn, "replay-fuera")["replayLocked"] is False


def _reloj(step: dict) -> str:
    marca = re.search(r"reloj (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", step["notes"])
    assert marca is not None, f"«{step['label']}» no dice el reloj del replay"
    return marca.group(1)


def test_el_paso_adelante_y_atras_se_deshacen(drawn: dict) -> None:
    """Con la vela en formación puesta, un paso arma un trozo más de la vela en
    curso: el reloj avanza aunque la última vela CERRADA siga siendo la misma."""
    inicio = _step(drawn, "replay-inicio")
    paso = _step(drawn, "replay-paso")
    atras = _step(drawn, "replay-atras")
    assert _reloj(paso) > _reloj(inicio)
    assert _reloj(atras) == _reloj(inicio)
    assert paso["plot"]["lastBar"] >= inicio["plot"]["lastBar"]


def test_h4_se_arma_con_h1_y_no_con_h3(drawn: dict) -> None:
    """H3 va justo debajo de H4 en la lista, pero una H3 quedaría partida entre
    dos H4: la vela H4 en curso se forma en cuatro pasos de una hora, no en dos
    de tres."""
    notas = _step(drawn, "replay-paso")["notes"]
    assert re.search(r"vela en formación con 1 de 4 velas", notas), notas


def test_el_reloj_del_replay_sobrevive_al_cambio_de_par(drawn: dict) -> None:
    """Cambiar de par no puede reiniciar el replay: el reloj es uno solo."""
    antes = _step(drawn, "replay-en-h4")
    otro = _step(drawn, "replay-otro-par")
    vuelta = _step(drawn, "replay-con-zoom")
    assert otro["symbol"] == "EURUSD"
    assert "REPLAY" in otro["notes"]
    assert "REPLAY" in antes["notes"]
    assert "REPLAY" in vuelta["notes"]


def test_el_encuadre_manual_se_suelta_al_cambiar_de_par(drawn: dict) -> None:
    """El rango de precios del oro no encuadra nada del euro."""
    con_marcas = _step(drawn, "marcas-en-el-primer-par")
    tras_cambiar = _step(drawn, "marcas-en-el-segundo-par")
    assert con_marcas["zoomFree"] is False, "el recorrido llega aquí con zoom a mano"
    assert tras_cambiar["zoomFree"] is True


def test_el_zoom_a_mano_se_puede_soltar(drawn: dict) -> None:
    assert _step(drawn, "replay-con-zoom")["zoomFree"] is False
    assert _step(drawn, "replay-zoom-suelto")["zoomFree"] is True


def test_arrastrar_sobre_los_ejes_reescala(drawn: dict) -> None:
    precios = _step(drawn, "eje-precios-arrastrado")["lastRelayout"]
    fechas = _step(drawn, "eje-fechas-arrastrado")["lastRelayout"]
    assert "yaxis.range" in precios
    assert "xaxis.range" in fechas


# --- La caja simulada ------------------------------------------------------------


def _caja(step: dict) -> dict:
    shapes = {shape["name"]: shape for shape in step["plot"]["sim"]}
    return {
        "entry": shapes["sim-entrada"]["y0"],
        "target": shapes["sim-objetivo"]["y1"],
        "stop": shapes["sim-riesgo"]["y1"],
    }


def test_el_boton_arma_y_escape_desarma(drawn: dict) -> None:
    assert _step(drawn, "sim-armado")["simArmed"] == "long"
    assert _step(drawn, "sim-desarmado")["simArmed"] is None
    assert _step(drawn, "sim-desarmado")["plot"]["sim"] == []


def test_la_caja_nace_con_el_objetivo_al_doble_del_riesgo(drawn: dict) -> None:
    caja = _caja(_step(drawn, "sim-largo"))
    riesgo = caja["entry"] - caja["stop"]
    objetivo = caja["target"] - caja["entry"]
    assert riesgo > 0
    assert objetivo == pytest.approx(2 * riesgo, rel=0.02)
    assert _step(drawn, "sim-largo")["simReadout"] == "R:R 1:2 · automático"


def test_arrastrar_el_stop_mueve_solo_el_stop(drawn: dict) -> None:
    antes = _caja(_step(drawn, "sim-largo"))
    despues = _caja(_step(drawn, "sim-stop-arrastrado"))
    assert despues["entry"] == antes["entry"]
    assert despues["target"] == antes["target"]
    assert despues["stop"] < antes["stop"]


def test_arrastrar_la_entrada_mueve_la_caja_entera(drawn: dict) -> None:
    antes = _caja(_step(drawn, "sim-stop-arrastrado"))
    despues = _caja(_step(drawn, "sim-entrada-arrastrada"))
    salto = despues["entry"] - antes["entry"]
    assert salto > 0
    assert despues["stop"] == pytest.approx(antes["stop"] + salto, abs=0.02)
    assert despues["target"] == pytest.approx(antes["target"] + salto, abs=0.02)


def test_el_rr_se_mide_mientras_se_arrastra(drawn: dict) -> None:
    """No se elige: sale de la caja, y se rehace en cada píxel del gesto.

    Lo que lo demuestra es que el panel ya decía otra cosa ANTES de soltar el
    botón del ratón: el número se lee mientras se coloca el objetivo, que es
    cuando se mira, y no al terminar el gesto.
    """
    antes = _step(drawn, "sim-objetivo-a-mano")["simReadout"]
    step = _step(drawn, "sim-objetivo-en-vuelo")
    assert step["simReadoutEnVuelo"].startswith("R:R 1:")
    assert step["simReadoutEnVuelo"] != antes
    assert step["simReadout"] == step["simReadoutEnVuelo"]


def test_en_corto_el_objetivo_va_por_debajo(drawn: dict) -> None:
    caja = _caja(_step(drawn, "sim-corto"))
    assert caja["target"] < caja["entry"] < caja["stop"]


def test_la_caja_se_puede_quitar(drawn: dict) -> None:
    assert _step(drawn, "sim-quitado")["plot"]["sim"] == []
    assert _step(drawn, "sim-quitado")["simClearDisabled"] is True


# --- La cuenta simulada ----------------------------------------------------------


def test_sin_caja_no_hay_nada_que_apuntar(drawn: dict) -> None:
    intacta = _step(drawn, "cuenta-intacta")
    assert intacta["account"]["resultsDisabled"] == [True, True, True]
    con_caja = _step(drawn, "cuenta-con-caja")
    assert con_caja["account"]["resultsDisabled"] == [False, False, False]


def _saldo(step: dict) -> str:
    """El saldo del resumen, sin el separador de millares.

    El separador depende de los datos de locale del entorno: node sin ICU
    completo escribe «1020,00 $» donde el navegador escribe «1.020,00 $». Lo que
    se está comprobando es la aritmética, no el formateo de la plataforma.
    """
    return step["account"]["summary"].split(" · ")[0].replace(".", "")


def test_ganar_suma_el_riesgo_por_el_rr_y_perder_lo_resta(drawn: dict) -> None:
    """50 $ al 2 % son 1,00 $ de riesgo; una caja 1:2 ganada suma 2,00 $."""
    assert _saldo(_step(drawn, "cuenta-ganada")) == "52,00 $"
    assert _saldo(_step(drawn, "cuenta-perdida")) == "51,00 $"


def test_el_break_even_cuenta_como_operacion_y_no_mueve_el_saldo(drawn: dict) -> None:
    perdida = _step(drawn, "cuenta-perdida")["account"]["summary"]
    be = _step(drawn, "cuenta-break-even")["account"]["summary"]
    assert be.split("·")[0] == perdida.split("·")[0]
    assert "3 operaciones" in be


def test_el_historial_copiado_dice_sobre_que_par_se_dibujo(drawn: dict) -> None:
    texto = _step(drawn, "cuenta-copiada")["copiado"]
    assert "par" in texto
    assert "XAUUSD (oro)" in texto
    assert "NO ES DINERO" not in texto  # eso va en el estado, no en el historial
    assert "no hay orden ninguna detrás" in texto


def test_deshacer_devuelve_el_saldo_y_la_caja(drawn: dict) -> None:
    antes = _step(drawn, "cuenta-break-even")
    despues = _step(drawn, "cuenta-deshecha")
    assert "2 operaciones" in despues["account"]["summary"]
    assert "3 operaciones" in antes["account"]["summary"]
    assert despues["plot"]["sim"] != []


def test_cambiar_el_capital_reescala_la_curva_entera(drawn: dict) -> None:
    """De cada operación se guarda su múltiplo de riesgo, no los euros."""
    step = _step(drawn, "cuenta-recapitalizada")
    assert step["account"]["initial"] == "1000"
    # 1000 $ al 2 % son 20 $ de riesgo: las dos operaciones que quedan (+2R, -1R)
    # valen ahora +40 $ y -20 $.
    assert _saldo(step) == "1020,00 $"


def test_el_riesgo_fijo_no_depende_del_capital(drawn: dict) -> None:
    step = _step(drawn, "cuenta-riesgo-fijo")
    assert step["account"]["mode"] == "cash"
    assert _saldo(step) == "1025,00 $"


def test_reiniciar_borra_las_operaciones(drawn: dict) -> None:
    step = _step(drawn, "cuenta-reiniciada")["account"]
    assert "0 operaciones" in step["summary"]
    assert step["undoDisabled"] is True


# --- Recuadros y líneas a mano ----------------------------------------------------


def test_los_botones_llevan_los_nombres_de_la_configuracion(drawn: dict) -> None:
    assert drawn["rectLabels"] == ["Zona 1", "Zona 2", "Zona 3"]
    assert drawn["lineLabels"] == ["Nivel 1", "Nivel 2", "Nivel 3"]


def test_el_recuadro_se_arma_se_planta_y_se_arrastra(drawn: dict) -> None:
    assert _step(drawn, "rect-sin-nada")["plot"]["rect"] == []
    assert _step(drawn, "rect-armado")["rectArmed"] == "Zona 1"
    assert _step(drawn, "rect-desarmado")["rectArmed"] is None

    plantado = _step(drawn, "rect-plantado")["plot"]["rect"]
    assert len(plantado) == 1
    assert plantado[0]["dash"] == "dot", "los recuadros a mano van punteados"
    assert plantado[0]["label"] == "Zona 1 1 (a mano)"

    antes = _step(drawn, "rect-plantado")["plot"]["rect"][0]
    techo = _step(drawn, "rect-techo-arrastrado")["plot"]["rect"][0]
    assert techo["y1"] > antes["y1"]
    assert techo["y0"] == antes["y0"]


def test_el_recuadro_se_mueve_entero_por_dentro(drawn: dict) -> None:
    antes = _step(drawn, "rect-techo-arrastrado")["plot"]["rect"][0]
    despues = _step(drawn, "rect-movido")["plot"]["rect"][0]
    alto_antes = antes["y1"] - antes["y0"]
    alto_despues = despues["y1"] - despues["y0"]
    assert alto_despues == pytest.approx(alto_antes, abs=0.02)
    assert despues["y0"] < antes["y0"]


def test_los_recuadros_se_numeran_por_nombre(drawn: dict) -> None:
    tercero = _step(drawn, "rect-tercero")["plot"]["rect"]
    etiquetas = [shape["label"] for shape in tercero]
    assert etiquetas == ["Zona 1 1 (a mano)", "Zona 2 1 (a mano)", "Zona 1 2 (a mano)"]


def test_deshacer_quita_el_ultimo_recuadro(drawn: dict) -> None:
    assert len(_step(drawn, "rect-deshecho")["plot"]["rect"]) == 2


def test_la_linea_nace_horizontal_y_se_inclina_por_un_extremo(drawn: dict) -> None:
    plantada = _step(drawn, "linea-plantada")["plot"]["line"][0]
    assert plantada["y0"] == plantada["y1"], "nace horizontal"
    assert plantada["dash"] is None, "las líneas a mano van continuas"

    inclinada = _step(drawn, "linea-inclinada")["plot"]["line"][0]
    assert inclinada["y1"] > inclinada["y0"]
    assert inclinada["y0"] == plantada["y0"], "el extremo izquierdo se queda"


def test_la_linea_se_mueve_entera_por_dentro(drawn: dict) -> None:
    antes = _step(drawn, "linea-inclinada")["plot"]["line"][0]
    despues = _step(drawn, "linea-movida")["plot"]["line"][0]
    pendiente_antes = antes["y1"] - antes["y0"]
    pendiente_despues = despues["y1"] - despues["y0"]
    assert pendiente_despues == pytest.approx(pendiente_antes, abs=0.02)
    assert despues["y0"] < antes["y0"]


def test_cada_nombre_lleva_su_color(drawn: dict) -> None:
    segunda = _step(drawn, "linea-segunda")["plot"]["line"]
    colores = {shape["color"] for shape in segunda}
    assert len(colores) == 2


# --- Las marcas son de cada par ----------------------------------------------------


def test_lo_marcado_en_un_par_no_se_ve_en_el_otro(drawn: dict) -> None:
    primero = _step(drawn, "marcas-en-el-primer-par")["plot"]
    segundo = _step(drawn, "marcas-en-el-segundo-par")["plot"]
    assert primero["rect"] and primero["line"]
    assert segundo["rect"] == []
    assert segundo["line"] == []


def test_volver_a_un_par_devuelve_lo_que_estaba_dibujado(drawn: dict) -> None:
    antes = _step(drawn, "marcas-en-el-primer-par")["plot"]
    vuelta = _step(drawn, "marcas-de-vuelta")["plot"]
    assert len(vuelta["rect"]) == len(antes["rect"])
    assert len(vuelta["line"]) == len(antes["line"])


def test_limpiar_se_lleva_todas_las_marcas(drawn: dict) -> None:
    limpias = _step(drawn, "marcas-limpias")["plot"]
    assert limpias["rect"] == []
    assert limpias["line"] == []


# --- Auditoría ciega ----------------------------------------------------------------


def test_la_misma_semilla_reabre_la_misma_ventana(drawn: dict) -> None:
    primera = _step(drawn, "ciega")
    repetida = _step(drawn, "ciega-repetida")
    assert (primera["from"], primera["to"]) == (repetida["from"], repetida["to"])
    assert "AUDITORÍA CIEGA" in primera["notes"]
    assert "12345" in primera["notes"]


def test_salir_de_la_ciega_devuelve_el_periodo(drawn: dict) -> None:
    assert "AUDITORÍA CIEGA" not in _step(drawn, "ciega-fuera")["notes"]
