"""El explorador de velas: lo que calcula el motor y lo que marca la mano.

Es la única herramienta visual del proyecto y lo que se comprueba aquí es doble:

  · que el CHASIS funciona —los cuatro pares, las temporalidades, la ventana, el
    replay, el zoom y las herramientas de mano—, y
  · que la ÚNICA capa calculada que se dibuja es la de los rangos CRT. Cualquier
    otra traza o forma que no sea precio ni marca de mano es un error, y el test
    lo dice con nombre.

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
    H4,
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
    BULLISH,
    HAND_COLORS,
    RangeLayer,
    bar_counts,
    build_payload,
    payload_size,
    render_explorer,
)
from chronos.interface.crt_layer import build_ranges, range_layer
from tests.conftest import make_m1_history

#: Dos pares con precios de escalas muy distintas: es lo que obliga a que los
#: decimales, el pip y el eje sean POR PAR y no una constante del explorador.
GOLD = SymbolConfig(symbol="XAUUSD", label="XAUUSD (oro)", bid_path="x.parquet", decimals=2)
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
        for timeframe in config.ordered_timeframes
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


@pytest.fixture(scope="module")
def layer(run: ChartRun) -> RangeLayer:
    """La capa calculada, compuesta como la compone la CLI.

    El explorador no la detecta: la recibe. Aquí se hace lo mismo que en el
    punto de composición para dibujar exactamente lo que se dibujaría de verdad.
    """
    return range_layer(build_ranges(run))


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


def test_cada_par_lleva_sus_velas_en_las_cuatro_temporalidades(run: ChartRun) -> None:
    counts = bar_counts(build_payload(run))
    assert set(counts) == {"XAUUSD", "EURUSD"}
    for charts in counts.values():
        assert set(charts) == {DAILY, H4, H1, M15}
        assert charts[M15] > charts[H1] > charts[H4] > charts[DAILY]


def test_la_duracion_de_la_vela_se_mide_sobre_las_velas(run: ChartRun) -> None:
    """El replay la necesita para saber cuándo cerró cada una."""
    spans = build_payload(run)["symbols"][0]["spans"]
    assert spans[M15] == 15
    assert spans[H1] == 60
    assert spans[H4] == 240
    # Con ancla de sesión el diario no dura siempre lo mismo, pero la moda sí.
    assert spans[DAILY] == 1440


def test_el_payload_lleva_una_sola_capa_calculada_y_se_llama_por_su_nombre(
    run: ChartRun,
) -> None:
    """Los rangos CRT y nada más.

    Si un día aparece una clave que no sea de las de abajo, será porque alguien
    ha metido otra capa calculada y este test tiene que enterarse.
    """
    payload = build_payload(run)
    assert set(payload) == {
        "meta", "colors", "labels", "keys", "marks", "crt", "symbols", "unavailable"
    }
    for symbol in payload["symbols"]:
        assert set(symbol) == {
            "id", "label", "decimals", "side", "provenance",
            "charts", "spans", "bars", "skipped", "ranges",
        }


def test_sin_capa_el_payload_lo_dice_en_vez_de_callarse(run: ChartRun) -> None:
    """Un explorador sin rangos tiene que poder dibujarse igual."""
    payload = build_payload(run)
    assert payload["crt"]["timeframe"] is None
    assert all(symbol["ranges"] == [] for symbol in payload["symbols"])


def test_los_rangos_viajan_por_par_con_lo_que_hace_falta_para_dibujarlos(
    run: ChartRun, layer: RangeLayer
) -> None:
    payload = build_payload(run, ranges=layer)

    assert payload["crt"]["timeframe"] == DAILY
    assert payload["crt"]["label"] == "Diario"
    assert payload["crt"]["tp"] == "TP D1"
    assert payload["crt"]["description"]

    rangos = payload["symbols"][0]["ranges"]
    assert rangos, "la fixture tiene que dar algún rango que dibujar"
    assert set(rangos[0]) == {
        "id", "dir", "up", "ref", "confirm", "known", "high", "low", "manip",
        "target", "invalidation", "size", "sizeAtr", "status", "resolved",
        "resolvedAt", "candles", "ambiguous",
    }
    for item in rangos:
        # La vela de referencia va antes de la de confirmación, y el rango no
        # existe hasta que ésta CIERRA.
        assert item["ref"] < item["confirm"] < item["known"]
        assert item["low"] < item["high"]
        assert (item["resolved"] is None) == (item["resolvedAt"] is None)
        if item["resolved"] is not None:
            assert item["resolved"] > item["confirm"]
            assert item["resolvedAt"] > item["known"]


def test_cada_par_lleva_sus_propios_rangos(run: ChartRun, layer: RangeLayer) -> None:
    """El euro de la fixture es el oro reescalado: los mismos rangos, otros precios."""
    payload = build_payload(run, ranges=layer)
    oro, euro = payload["symbols"][0]["ranges"], payload["symbols"][1]["ranges"]

    assert len(oro) == len(euro)
    assert oro[0]["high"] > 100 > euro[0]["high"]


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


def test_la_cabecera_dice_qué_capa_calculada_lleva(run: ChartRun, layer: RangeLayer) -> None:
    html = render_explorer(run, ranges=layer)
    assert "capa calculada: rangos CRT de Diario" in html
    assert "XAUUSD (oro) · EURUSD" in html


def test_sin_capa_la_cabecera_no_promete_ninguna(run: ChartRun) -> None:
    assert "sin ninguna capa calculada" in render_explorer(run)


def test_el_json_embebido_no_puede_cerrar_la_etiqueta(run: ChartRun) -> None:
    html = render_explorer(run)
    datos = html.split('<script id="explorer-data" type="application/json">')[1]
    datos = datos.split("</script>")[0]
    assert "</" not in datos


def test_el_tamano_del_payload_se_puede_vigilar(run: ChartRun) -> None:
    assert payload_size(build_payload(run)) > 0


# --- El JavaScript, contra un DOM simulado ------------------------------------


def _draw(run: ChartRun, layer: RangeLayer, tmp_path: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible: no se puede ejecutar el JavaScript")

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps(build_payload(run, ranges=layer), default=str), encoding="utf-8"
    )
    stub = Path(__file__).parent / "explorer_dom_stub.js"
    output = subprocess.run(
        [node, str(stub), str(ASSETS / "explorer.js"), str(payload_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(output.stdout)


@pytest.fixture(scope="module")
def drawn(
    run: ChartRun, layer: RangeLayer, tmp_path_factory: pytest.TempPathFactory
) -> dict:
    return _draw(run, layer, tmp_path_factory.mktemp("explorer"))


def _step(resultado: dict, label: str) -> dict:
    return next(step for step in resultado["steps"] if step["label"] == label)


def test_el_explorador_se_dibuja_sin_errores(drawn: dict) -> None:
    assert not drawn["unknownElements"], (
        f"el explorador busca elementos que la plantilla no define: {drawn['unknownElements']}"
    )
    assert drawn["chartTabs"] == ["Diario", "H4", "H1", "M15"]
    assert drawn["presetLabels"][0] == "Todo"
    assert _step(drawn, "todo")["plot"]["target"] == "chart"


def test_la_unica_traza_calculada_es_la_de_los_rangos(drawn: dict) -> None:
    """La promesa del proyecto, comprobada en cada paso del recorrido.

    Este test nació diciendo que NINGUNA traza podía ser otra cosa que velas.
    Al escribirse la primera regla se actualizó a propósito: ahora dice cuál es
    la capa calculada que se admite. Cualquier otra sigue siendo un error.
    """
    for step in drawn["steps"]:
        for name in step["plot"]["calculated"]:
            assert name.startswith("Rangos CRT"), (
                f"en «{step['label']}» hay una traza que no es ni precio ni rangos: {name}"
            )


def test_en_shapes_solo_hay_marcas_a_mano_y_rangos_crt(drawn: dict) -> None:
    for step in drawn["steps"]:
        assert step["plot"]["shapes"] == 0, (
            f"en «{step['label']}» hay formas que no ha puesto ni la mano ni el motor"
        )


def test_el_estado_separa_lo_que_calcula_el_motor_de_lo_que_pones_tu(drawn: dict) -> None:
    """Un rectángulo encima del precio no dice por sí solo quién lo dibujó."""
    notas = _step(drawn, "todo")["notes"]
    assert "LO QUE CALCULA EL MOTOR SON LOS RANGOS CRT DE DIARIO Y NADA MÁS" in notas
    assert "Todo lo demás que se dibuje encima del precio lo pones tú" in notas


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
        for timeframe in (DAILY, H4, H1, M15)
    }
    assert velas[M15] > velas[H1] > velas[H4] > velas[DAILY]


def test_las_teclas_saltan_de_grafico(drawn: dict) -> None:
    assert _step(drawn, f"teclado-tf-{H4}")["chart"] == H4
    assert _step(drawn, f"teclado-tf-{M15}")["chart"] == M15
    # Con el foco en un campo la tecla escribe y no salta.
    assert _step(drawn, "teclado-tf-en-un-campo")["chart"] == M15


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
    assert [trace["type"] for trace in lineas["traces"]] == ["scatter"]
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


# --- Los rangos CRT: la capa que calcula el motor ---------------------------------


def _rangos(step: dict) -> list[dict]:
    return [shape for shape in step["plot"]["crt"] if shape["name"].startswith("crt-rango-")]


def _objetivos(step: dict) -> list[dict]:
    return [shape for shape in step["plot"]["crt"] if shape["name"].startswith("crt-tp-")]


def _marcas(step: dict) -> dict | None:
    trazas = [
        trace for trace in step["plot"]["traces"]
        if str(trace["name"]).startswith("Rangos CRT")
    ]
    return trazas[0] if trazas else None


def test_los_rangos_se_dibujan_sobre_su_temporalidad(drawn: dict) -> None:
    diario = _step(drawn, "crt-en-su-temporalidad")
    assert _rangos(diario), "en Diario tiene que haber rangos dibujados"
    # Un rectángulo por rango y una línea de objetivo por rango.
    assert len(_objetivos(diario)) == len(_rangos(diario))
    # Y la mecha de manipulación, en una sola traza de puntos.
    assert _marcas(diario)["points"] == len(_rangos(diario))


def test_en_otra_temporalidad_no_se_dibuja_ningun_rango(drawn: dict) -> None:
    """Un rango diario encima de H4 ocuparía la pantalla y no se leería."""
    otra = _step(drawn, "crt-en-otra-temporalidad")
    assert otra["chart"] != DAILY
    assert otra["plot"]["crt"] == []
    assert _marcas(otra) is None
    assert "se dibujan sólo sobre ese gráfico" in otra["notes"]


def test_el_rectangulo_va_por_debajo_del_precio_y_dice_su_estado_con_el_trazo(
    drawn: dict,
) -> None:
    rangos = _rangos(_step(drawn, "crt-en-su-temporalidad"))
    assert all(shape["layer"] == "below" for shape in rangos), (
        "los rangos no pueden tapar las velas"
    )
    trazos = {shape["dash"] for shape in rangos}
    assert trazos == {"solid", "dot"}, (
        "el trazo distingue el rango vivo del resuelto y el recorrido tiene de los dos"
    )
    assert all(shape["y0"] < shape["y1"] for shape in rangos)


def test_la_linea_del_objetivo_lleva_su_etiqueta(drawn: dict) -> None:
    etiquetas = {shape["label"] for shape in _objetivos(_step(drawn, "crt-en-su-temporalidad"))}
    assert "TP D1" in etiquetas
    assert all(shape["dash"] == "dash" for shape in _objetivos(_step(drawn, "crt-en-su-temporalidad")))


def test_ocultar_los_resueltos_deja_solo_los_vivos(drawn: dict) -> None:
    todos = _rangos(_step(drawn, "crt-en-su-temporalidad"))
    vivos = _rangos(_step(drawn, "crt-sin-resueltos"))

    assert 0 < len(vivos) < len(todos)
    assert {shape["dash"] for shape in vivos} == {"solid"}
    assert "los resueltos están ocultos" in _step(drawn, "crt-sin-resueltos")["notes"]
    assert _step(drawn, "crt-sin-resueltos")["crtResolvedChecked"] is False
    assert _step(drawn, "crt-en-su-temporalidad")["crtResolvedChecked"] is True


def test_el_panel_dice_hacia_donde_va_el_precio(drawn: dict) -> None:
    panel = _step(drawn, "crt-en-su-temporalidad")["crtBias"]
    assert panel.startswith("Sesgo D1: ")
    if "SIN TARGET" not in panel:
        assert "Target" in panel and "Invalidación" in panel


def test_el_sesgo_y_la_tabla_se_ven_tambien_fuera_del_diario(drawn: dict) -> None:
    """El sesgo diario manda se mire lo que se mire."""
    otra = _step(drawn, "crt-en-otra-temporalidad")
    assert otra["crtBias"].startswith("Sesgo D1: ")
    assert len(otra["crtRows"]) > 1


def test_la_tabla_lista_los_rangos_conocidos(drawn: dict) -> None:
    filas = _step(drawn, "crt-en-su-temporalidad")["crtRows"]
    assert filas[0] == [
        "nº", "dirección", "confirmado (UTC)", "rango", "manipulación",
        "objetivo", "tamaño", "estado", "resuelto (UTC)", "velas",
    ]
    datos = filas[1:]
    assert datos, "tiene que haber rangos que listar"
    assert all(len(fila) == len(filas[0]) for fila in datos)
    # La más reciente arriba.
    assert datos[0][2] >= datos[-1][2]
    assert "los detecta el motor" in _step(drawn, "crt-en-su-temporalidad")["crtCaption"]


def test_en_replay_no_se_dibuja_ningun_rango_mas_alla_del_reloj(drawn: dict) -> None:
    """Lo que el motor no sabía todavía no puede estar en la pantalla."""
    for label in ("crt-replay-inicio", "crt-replay-paso"):
        step = _step(drawn, label)
        assert step["crtMaxX"] is not None, f"«{label}» no dibuja ningún rango"
        assert step["crtMaxX"][:16] <= _reloj(step)


def test_el_replay_solo_ensena_los_rangos_ya_confirmados(drawn: dict) -> None:
    replay = _step(drawn, "crt-replay-inicio")
    completo = _step(drawn, "crt-en-su-temporalidad")
    assert len(_rangos(replay)) < len(_rangos(completo))
    assert len(replay["crtRows"]) < len(completo["crtRows"])


def test_la_venda_tapa_tambien_lo_que_calcula_el_motor(drawn: dict) -> None:
    """Enseñar el sesgo mientras se pide una opinión a ciegas sería contar el final."""
    ciega = _step(drawn, "ciega")
    revelada = _step(drawn, "ciega-revelada")

    assert ciega["plot"]["crt"] == []
    assert ciega["crtBias"] == "Sesgo D1: TAPADO"
    assert ciega["crtRows"][1][0].startswith("la auditoría ciega tapa")
    assert "la venda tapa también los RANGOS CRT" in ciega["notes"]
    assert revelada["plot"]["crt"], "al revelar tienen que aparecer"


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
