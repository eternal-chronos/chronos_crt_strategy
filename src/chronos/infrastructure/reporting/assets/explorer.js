/* Explorador de velas multi-par.
 *
 * El chasis del gráfico: velas o cierres, cuatro temporalidades, cuatro pares,
 * ventana de fechas, zoom, replay y las herramientas con las que el propietario
 * marca a mano encima del precio. Encima, TRES capas calculadas: la caja de las
 * 02:00 NY de H3, que se ve en H3 y en H1, la señal de confirmación de H1, sólo
 * en H1, y su confirmación en H4, sólo en H4. Llegan resueltas del motor y aquí sólo se pintan, cada una con
 * sus trazas, su entrada en la leyenda y su línea en el estado.
 *
 * El estado visible es mínimo y la figura se reconstruye entera en cada cambio
 * con Plotly.react. Es más barato de razonar que llevar la cuenta de índices de
 * traza.
 *
 * La ventana de fechas recorta los datos en vez de limitarse a mover el eje: los
 * dos ejes se autoescalan al tramo y el dibujo no arrastra ocho años de velas en
 * cada paso. Las marcas de tiempo viajan como minutos desde la época en UTC, así
 * que filtrar por fecha es comparar enteros.
 *
 * TODO LO DE ESTE FICHERO ES DIBUJO. Ni la navegación, ni el replay, ni las
 * marcas a mano calculan nada: el payload llega ya armado desde Python y aquí
 * sólo se elige qué parte de él se pinta.
 *
 * EL PAR ES ESTADO, no otro fichero. Los cuatro históricos viajan dentro del
 * mismo HTML y cambiar de par es cambiar de qué array se lee. Cada par lleva SUS
 * decimales —el pip de EURUSD no es el de XAUUSD— y SUS marcas a mano: un
 * recuadro puesto en el oro a 2.400 no significa nada sobre el euro, así que al
 * cambiar de par se guardan y se reponen las del que se abre.
 *
 * El replay es la excepción a la que hay que mirar con lupa. Reproduce la
 * historia paso a paso desde una fecha y en cada paso dibuja sólo hasta donde
 * llega el reloj. Como las velas van etiquetadas al inicio del intervalo, saber
 * eso exige el cierre y no la etiqueta: la vela de `t` cierra en `t + span(tf)`.
 * Sigue sin calcularse nada: se retrasa lo que se enseña.
 */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("explorer-data").textContent);
  var COLORS = DATA.colors;
  var SESSION_TZ = DATA.meta.sessionTimezone;
  // El nombre con el que se escribe: `Etc/GMT+4` es el UTC-4 y se lee al revés.
  var SESSION_TZ_LABEL = DATA.meta.sessionTimezoneLabel || SESSION_TZ;
  var DAY = 1440;

  var PRESETS = [
    { id: "all", label: "Todo", days: null },
    { id: "5y", label: "5 años", days: 1825 },
    { id: "2y", label: "2 años", days: 730 },
    { id: "1y", label: "1 año", days: 365 },
    { id: "6m", label: "6 meses", days: 182 },
    { id: "3m", label: "3 meses", days: 91 },
    { id: "1m", label: "1 mes", days: 30 },
    { id: "1w", label: "1 semana", days: 7 }
  ];

  var state = {
    /* El par que se está mirando. Es un índice y no un nombre porque el orden de
     * los botones es el de la configuración y hay que poder recorrerlo. */
    symbol: 0,
    chart: DATA.symbols[0].charts[0],
    view: "candles",
    preset: "3m",
    from: null,
    to: null,
    blind: false,      // auditoría ciega en curso
    revealed: false,
    seed: null,
    seedTyped: false,
    scope: null,       // rango dentro del que se sortean las ventanas ciegas
    /* Replay. `cursor` es el índice de la última vela CERRADA del gráfico
     * actual; `sub`, cuántas velas de la temporalidad inferior lleva formadas la
     * siguiente. Con `sub > 0` hay una vela a medio hacer en el borde derecho. */
    replay: false,
    cursor: 0,
    sub: 0,
    /* El reloj, en minutos: hasta dónde ha visto el mercado el replay. Es el
     * estado canónico y no depende de la temporalidad que se esté mirando; el
     * par (`cursor`, `sub`) es la lectura de ese reloj en el gráfico actual.
     * Guardarlo aparte permite ir y volver entre temporalidades —y entre
     * PARES— sin perder resolución. */
    at: 0,
    forming: true,     // armar la vela en curso con la temporalidad inferior
    playing: false,
    speed: 700,        // milisegundos entre pasos
    window: 180,       // velas a la vista durante el replay
    resume: null,      // periodo al que se vuelve al salir del replay
    /* Encuadre manual. `Plotly.react` vuelve a decidir los ejes en cada dibujo,
     * así que el zoom que el usuario deja con la rueda o arrastrando se perdía
     * en cada paso del replay. Aquí se guarda lo último que fijó a mano —x en
     * minutos, y en precio— y se le vuelve a imponer al gráfico. Se suelta con
     * «Ajustar», con doble clic sobre el gráfico, y al cambiar de par: el rango
     * de precios del oro no encuadra nada del euro. */
    zoom: { x: null, y: null },
    /* Simulador de entradas. `sim` es la caja plantada a mano —lado, entrada,
     * stop, objetivo y el tramo que ocupa— y `arming` el botón que espera el
     * clic que la planta. Es DIBUJO: no sale del explorador, no lo lee nadie y
     * no hay ninguna orden detrás. */
    sim: null,
    arming: null,
    /* Recuadros marcados a mano. Cada uno es un rectángulo —`kind`, `from`,
     * `to`, `low`, `high`— que planta el propietario para señalar dónde ve algo
     * que todavía no tiene regla que lo detecte. */
    rects: [],
    /* Líneas trazadas a mano. Cada una es un segmento —`kind`, `from`, `to` y el
     * precio de cada extremo, `left` y `right`—. */
    lines: [],
    /* Las marcas de los pares que no se están mirando, guardadas por símbolo.
     * Volver a un par tiene que devolver lo que se dejó dibujado en él. */
    stashed: {},
    /* Cuenta simulada. El capital de partida, cómo se mide el riesgo de cada
     * operación y las que se llevan apuntadas. De cada una se guarda su MÚLTIPLO
     * DE RIESGO, no los euros: el dinero se recalcula entero en cada dibujo, así
     * que cambiar el capital o el riesgo reescala la curva en vez de dejar
     * cifras de una configuración que ya no está puesta.
     *
     * Es UNA cuenta para los cuatro pares, no una por par: el capital del
     * propietario es uno solo y lo que se quiere ver es la racha entera. Cada
     * operación apuntada dice sobre qué par se dibujó. */
    account: {
      initial: 50,
      mode: "percent",   // percent | cash
      risk: 2,
      trades: [],
      copied: null       // resultado del último «Copiar», para poder decirlo
    }
  };

  /* La corrida puede traer con qué cuenta se mira —capital y riesgo por
   * operación—. Sin ella la cuenta arranca donde arranca y sigue siendo una
   * herramienta de mano: esto es el valor de salida, no un candado. */
  if (DATA.account) {
    Object.keys(DATA.account).forEach(function (key) {
      state.account[key] = DATA.account[key];
    });
  }

  var timer = null;    // temporizador de la reproducción automática

  /* Auditoría ciega. El sorteo va con semilla y la semilla se enseña: una
   * ventana "al azar" que no se puede volver a abrir no sirve para discutirla
   * después con nadie. mulberry32: pequeño, determinista y suficiente para
   * elegir un día. */
  function rng(seed) {
    var value = seed >>> 0;
    return function () {
      value = (value + 0x6D2B79F5) >>> 0;
      var t = Math.imul(value ^ (value >>> 15), 1 | value);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // --- El par -----------------------------------------------------------------

  function sym() { return DATA.symbols[state.symbol]; }
  function charts() { return sym().charts; }
  function decimals() { return sym().decimals; }

  /* Un pip es la última cifra que se enseña del precio: con cinco decimales,
   * 0,00001. Va POR PAR y no fijo, que es justo lo que cambia entre el oro y un
   * cruce de divisas, y de él salen todas las distancias que se miden a mano. */
  function pip() { return Math.pow(10, -decimals()); }

  // --- Tiempo -----------------------------------------------------------------

  function toDate(minute) { return new Date(minute * 60000); }
  function iso(minute) { return toDate(minute).toISOString().replace("T", " ").slice(0, 19); }
  function dayOf(minute) { return toDate(minute).toISOString().slice(0, 10); }
  function dayStart(text) { return Math.floor(Date.parse(text + "T00:00:00Z") / 60000); }
  function dayEnd(text) { return dayStart(text) + DAY - 1; }
  function shiftDays(text, delta) { return dayOf(dayStart(text) + delta * DAY); }
  function spanDays(from, to) { return Math.max(1, Math.round((dayStart(to) - dayStart(from)) / DAY)); }

  var sessionFormat = new Intl.DateTimeFormat("sv-SE", {
    timeZone: SESSION_TZ, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false
  });

  function stamp(minute) {
    return iso(minute).slice(0, 16) + " UTC · " +
      sessionFormat.format(toDate(minute)).replace(",", "") + " " + SESSION_TZ_LABEL;
  }

  function price(value) { return value.toFixed(decimals()); }

  // --- Ventana ----------------------------------------------------------------

  function barsOf(timeframe) { return sym().bars[timeframe]; }
  function bars() { return barsOf(state.chart); }

  /* Duración de la vela de cada temporalidad, en minutos. Viene medida sobre las
   * velas desde Python: con ancla de sesión el diario no dura siempre lo mismo y
   * deducirla del nombre mentiría. */
  function span(timeframe) { return sym().spans[timeframe] || sym().spans[state.chart]; }

  function label(timeframe) { return DATA.labels[timeframe] || timeframe; }

  /* El reloj del replay: el minuto en que cerró la última vela del gráfico. Todo
   * lo posterior a esta marca es futuro y no se dibuja. */
  function now_() { return bars().t[state.cursor] + span(state.chart); }

  function bounds() {
    var t = bars().t;
    var first = dayOf(t[0]);
    var last = dayOf(t[t.length - 1]);
    if (state.replay) {
      var start = Math.max(0, state.cursor - state.window + 1);
      // Con encuadre manual el usuario puede haber alejado el zoom más allá de
      // las velas a la vista: si no se ampliara el corte, la mitad izquierda de
      // su pantalla saldría vacía.
      if (state.zoom.x) {
        start = Math.min(start, Math.max(0, lowerBound(t, state.zoom.x[0]) - 1));
      }
      var edge = now_();
      return {
        from: dayOf(t[start]), to: dayOf(edge), first: first, last: last,
        lo: t[start], hi: edge, cut: { start: start, end: state.cursor + 1 }
      };
    }
    if (state.from || state.to) {
      return range_(state.from || first, state.to || last, first, last);
    }
    var preset = PRESETS.filter(function (p) { return p.id === state.preset; })[0] || PRESETS[0];
    if (preset.days === null) { return range_(first, last, first, last); }
    var from = shiftDays(last, -preset.days);
    return range_(from < first ? first : from, last, first, last);
  }

  function range_(from, to, first, last) {
    return {
      from: from, to: to, first: first, last: last,
      lo: dayStart(from), hi: dayEnd(to)
    };
  }

  function slice(range) {
    if (range.cut) { return range.cut; }
    var t = bars().t;
    var start = lowerBound(t, range.lo);
    var end = lowerBound(t, range.hi + 1);
    return { start: start, end: end };
  }

  function lowerBound(values, target) {
    var low = 0, high = values.length;
    while (low < high) {
      var middle = (low + high) >> 1;
      if (values[middle] < target) { low = middle + 1; } else { high = middle; }
    }
    return low;
  }

  /* Con la venda puesta no se dibuja nada más que las velas: es el punto de la
   * prueba —mirar el gráfico pelado antes de ver lo que marcaste— y por eso el
   * apagón se hace en un único sitio: alcanza a la caja de las 02:00 y a las señales
   * de H1 y H4 calculadas, que no se ven hasta Revelar. Las marcas a mano sí: marcar es la prueba. */
  function blindfolded() { return state.blind && !state.revealed; }

  function rgba(hex, alpha) {
    var value = String(hex).replace("#", "");
    if (value.length !== 6) { return hex; }
    var r = parseInt(value.slice(0, 2), 16);
    var g = parseInt(value.slice(2, 4), 16);
    var b = parseInt(value.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  // --- Trazas de precio -------------------------------------------------------

  function priceTraces(cut) {
    var b = bars();
    var x = b.t.slice(cut.start, cut.end).map(iso);
    var open = b.o.slice(cut.start, cut.end);
    var high = b.h.slice(cut.start, cut.end);
    var low = b.l.slice(cut.start, cut.end);
    var close = b.c.slice(cut.start, cut.end);
    var text = b.t.slice(cut.start, cut.end).map(function (minute, i) {
      return sym().label + " · " + stamp(minute) +
        "<br>O " + price(open[i]) + " · H " + price(high[i]) +
        "<br>L " + price(low[i]) + " · C " + price(close[i]) +
        "<br>cuerpo [" + price(Math.min(open[i], close[i])) + ", " +
        price(Math.max(open[i], close[i])) + "]";
    });

    var half = formingCandle();

    if (state.view === "line") {
      if (half) { x = x.concat([iso(half.x)]); close = close.concat([half.c]); }
      return [{
        type: "scatter", mode: "lines", name: "Cierres " + label(state.chart),
        x: x, y: close, line: { color: COLORS.ink, width: 1.2 },
        text: text, hoverinfo: "text", hoverlabel: { align: "left" }
      }];
    }
    var traces = [{
      type: "candlestick", name: "Velas " + label(state.chart),
      x: x, open: open, high: high, low: low, close: close,
      increasing: { line: { color: COLORS.bullish, width: 1 }, fillcolor: COLORS.bullish },
      decreasing: { line: { color: COLORS.bearish, width: 1 }, fillcolor: COLORS.bearish },
      text: text, hoverinfo: "text", hoverlabel: { align: "left" }
    }];
    if (half) { traces.push(formingTrace(half)); }
    return traces;
  }

  // --- Caja de las 02:00 en H3 (capa calculada) ---------------------------------

  /* Las cajas llegan RESUELTAS del motor: de qué vela sale cada una, cuándo se
   * sabe y cuál la sustituye. Aquí no se decide nada: se
   * elige qué parte se enseña.
   *
   * En H3, que es donde se calculan, y en H1, donde se busca la señal; en el
   * resto no se dibuja nada calculado. Y SÓLO LA ACTUAL: la última que se sabe al cierre de la
   * última vela a la vista. Las de días anteriores y las sustituidas no se
   * dibujan. Una caja existe desde el cierre de la vela que
   * la hace existir —la anterior a las 02:00, la de las 02:00 si cerró fuera o
   * la que tocó el segundo extremo de la anterior— y se dibuja desde su vela
   * hasta las 12:00 NY; en replay, hasta el reloj. */
  var BOX_CHART = "H3";
  var BOX_CHARTS = [BOX_CHART, "H1"];
  var BOX_NAMES = {
    bearish: "Caja de las 02:00 · rango bajista",
    bullish: "Caja de las 02:00 · rango alcista",
    candle: "Caja de las 02:00 · vela"
  };

  function boxesShown() { return BOX_CHARTS.indexOf(state.chart) >= 0 && !blindfolded(); }

  /* La caja actual: la última que se sabe al cierre de la última vela del
   * tramo, si cae en él. Las cajas llegan en orden. */
  function visibleBoxes(cut) {
    var b = bars();
    if (!boxesShown() || cut.end <= cut.start) { return []; }
    var step = span(BOX_CHART);
    var lo = b.t[cut.start];
    var edge = b.t[cut.end - 1] + span(state.chart);   // cierre de la última vela a la vista
    var known = (sym().boxes || []).filter(function (box) { return box.known + step <= edge; });
    var box = known[known.length - 1];
    if (!box) { return []; }
    // Una que empezó antes del tramo se recorta a su borde: si no, estiraría el
    // eje hacia fechas que no se están mirando.
    var item = { box: box, from: Math.max(box.ref, lo), to: Math.min(box.until, edge) };
    return item.to > item.from ? [item] : [];
  }

  function boxGroup(box) { return box.kind === "rango" ? box.dir : "candle"; }

  function boxOrigin(box) {
    if (box.kind === "rango") {
      return "rango CRT " + (box.dir === "bearish" ? "bajista" : "alcista") +
        " vivo antes de las 02:00 NY · vela 1 " + stamp(box.ref);
    }
    if (box.kind === "vela_previa") {
      return "sin rango vivo antes de las 02:00 NY: la vela anterior, " + stamp(box.ref);
    }
    if (box.kind === "ruptura") {
      return "la vela de las 02:00 NY, " + stamp(box.ref) + ", que cerró fuera de la caja";
    }
    return "la vela " + stamp(box.ref) + ", que tocó el segundo extremo de la caja anterior";
  }

  function boxTraces(cut) {
    var shown = visibleBoxes(cut);
    return ["bearish", "bullish", "candle"].map(function (group) {
      var x = [], y = [], text = [];
      shown.filter(function (item) { return boxGroup(item.box) === group; }).forEach(function (item) {
        var box = item.box;
        var caption = "Caja de las 02:00 (calculada)<br>" + boxOrigin(box) +
          "<br>máximo " + price(box.high) + " · mínimo " + price(box.low) +
          "<br>vale hasta las 12:00 NY, " + stamp(box.until);
        [item.from, item.to, item.to, item.from, item.from].forEach(function (minute) { x.push(iso(minute)); });
        [box.high, box.high, box.low, box.low, box.high].forEach(function (value) { y.push(value); });
        for (var i = 0; i < 5; i++) { text.push(caption); }
        x.push(null); y.push(null); text.push(null);
      });
      return {
        type: "scatter", mode: "lines", name: BOX_NAMES[group],
        x: x, y: y, fill: "toself", fillcolor: rgba(COLORS.boxes[group], 0.12),
        line: { color: COLORS.boxes[group], width: 1.2 },
        text: text, hoverinfo: "text", hoveron: "points+fills", hoverlabel: { align: "left" }
      };
    }).filter(function (trace) { return trace.x.length; });
  }

  /* Qué se ve de la capa calculada y qué no. Se dice siempre: un H3 sin cajas
   * a la vista no es lo mismo que un Diario donde no se dibujan. */
  function boxCaption(cut) {
    if (BOX_CHARTS.indexOf(state.chart) < 0) {
      return "caja de las 02:00 (calculada): sólo se dibuja en H3 y en H1";
    }
    if (blindfolded()) {
      return "caja de las 02:00 (calculada): oculta hasta Revelar";
    }
    var current = visibleBoxes(cut)[0];
    return "caja de las 02:00 NY de H3 (calculada por el motor): el rango vivo antes de " +
      "las 02:00, o la vela anterior si no hay; la sustituye la de las 02:00 si cierra " +
      "fuera, o la vela que toque su segundo extremo; vale hasta las 12:00 NY; sólo la " +
      "actual, las anteriores no se dibujan · " +
      (current
        ? "actual: de " + price(current.box.low) + " a " + price(current.box.high)
        : "ninguna a la vista");
  }

  // --- Señales de confirmación en H1 y en H4 (capas calculadas) ---------------

  /* Llegan RESUELTAS del motor y aquí sólo se pintan, cada una en su gráfico:
   *
   * - H1: una como mucho por vela de H3, cuando H1 ha tocado un extremo de la
   *   caja y una vela de H1 le hace turtle soup a la inmediatamente anterior.
   * - H4: las de H1 que H4 confirma, la señal para buscar entradas: la vela de
   *   H4 en curso le hace turtle soup a la inmediatamente anterior, del mismo
   *   lado, mientras la de H1 sigue activa.
   *
   * De cada una, las dos líneas de la vela anterior —la del turtle soup, el
   * extremo barrido, continua; la del otro extremo, a trazos— desde esa vela
   * hasta que cierra la de H3 (en H1) o la de H4 en curso (en H4), y un
   * triángulo en la vela de la señal. Se ven todas las del tramo, cada una
   * desde que cierra la vela de H1 que la hace existir; en replay, sin pasar
   * del reloj. En H4 eso pasa con la vela de H4 todavía a medio hacer, así que
   * su borde es el reloj del replay y no el cierre de la última H4. */
  var SIGNAL_LAYERS = {
    H1: {
      key: "signals", title: "Señal de confirmación H1",
      names: {
        swept: "Señal H1 · línea del turtle soup",
        opposite: "Señal H1 · otro extremo",
        mark: "Señal H1 · confirmación"
      },
      known: function (signal) { return signal.t; },
      detail: function (signal) {
        return "tras tocar el " + (signal.dir === "bullish" ? "mínimo" : "máximo") +
          " de la caja de las 02:00<br>anticipa que la vela de H3 cierra dentro; hasta " +
          stamp(signal.until);
      },
      clock: false
    },
    H4: {
      key: "h4signals", title: "Señal de confirmación H4 · buscar entradas",
      names: {
        swept: "Señal H4 · línea del turtle soup",
        opposite: "Señal H4 · otro extremo",
        mark: "Señal H4 · buscar entradas"
      },
      known: function (signal) { return signal.known; },
      detail: function (signal) {
        return "la vela de H4 en curso, sobre la anterior (" + stamp(signal.from) + ")" +
          "<br>confirma la señal de H1 al cierre de la vela de H1 de " + stamp(signal.known);
      },
      clock: true
    }
  };

  function signalLayer() { return blindfolded() ? null : SIGNAL_LAYERS[state.chart] || null; }

  function visibleSignals(cut) {
    var layer = signalLayer();
    var b = bars();
    if (!layer || cut.end <= cut.start) { return []; }
    var hour = span("H1");
    var lo = b.t[cut.start];
    var edge = layer.clock && state.replay ? state.at : b.t[cut.end - 1] + span(state.chart);
    return (sym()[layer.key] || []).filter(function (signal) {
      return layer.known(signal) + hour <= edge && signal.t >= lo;
    }).map(function (signal) {
      return { signal: signal, from: Math.max(signal.from, lo), to: Math.min(signal.until, edge) };
    });
  }

  function signalCaption_(layer, signal) {
    return layer.title + " (calculada)<br>turtle soup " +
      (signal.dir === "bullish" ? "alcista" : "bajista") + " en " + stamp(signal.t) +
      "<br>línea del turtle soup " + price(signal.swept) + " · otro extremo " +
      price(signal.opposite) + "<br>" + layer.detail(signal);
  }

  function signalTraces(cut) {
    var layer = signalLayer();
    var shown = visibleSignals(cut);
    if (!shown.length) { return []; }
    var traces = ["swept", "opposite"].map(function (level) {
      var x = [], y = [], text = [];
      shown.forEach(function (item) {
        var caption = signalCaption_(layer, item.signal);
        x.push(iso(item.from), iso(item.to), null);
        y.push(item.signal[level], item.signal[level], null);
        text.push(caption, caption, null);
      });
      return {
        type: "scatter", mode: "lines", name: layer.names[level], x: x, y: y,
        line: { color: COLORS.signal, width: 1.4, dash: level === "swept" ? "solid" : "dash" },
        text: text, hoverinfo: "text", hoverlabel: { align: "left" }
      };
    });
    traces.push({
      type: "scatter", mode: "markers", name: layer.names.mark,
      x: shown.map(function (item) { return iso(item.signal.t); }),
      y: shown.map(function (item) { return item.signal.swept; }),
      marker: {
        color: COLORS.signal, size: 11,
        symbol: shown.map(function (item) {
          return item.signal.dir === "bullish" ? "triangle-up" : "triangle-down";
        })
      },
      text: shown.map(function (item) { return signalCaption_(layer, item.signal); }),
      hoverinfo: "text", hoverlabel: { align: "left" }
    });
    return traces;
  }

  function signalCaption(cut) {
    if (!SIGNAL_LAYERS[state.chart]) {
      return "señales (calculadas): la de H1 sólo se dibuja en H1 y la de H4 sólo en H4";
    }
    if (blindfolded()) {
      return "señal de " + state.chart + " (calculada): oculta hasta Revelar";
    }
    var count = visibleSignals(cut).length;
    var text = state.chart === "H1"
      ? "señal de confirmación en H1 (calculada por el motor): con la vela de H3 " +
        "abierta, H1 toca un extremo de la caja y una vela de H1 le hace turtle soup a la " +
        "anterior en ese lado; una por vela de H3"
      : "señal de confirmación en H4 para buscar entradas (calculada por el motor): con la " +
        "señal de H1 activa, la vela de H4 en curso le hace turtle soup a la anterior en el " +
        "mismo lado";
    return text + "; línea continua en el extremo barrido, a trazos en el otro · " +
      (count ? count + " a la vista" : "ninguna a la vista");
  }

  /* La vela en formación. Se arma con las velas de la temporalidad inferior que
   * ya han cerrado dentro del intervalo en curso: en H4, con las de H1. Va hueca
   * y en su propia traza porque no es una vela cerrada: es la forma de mirar
   * cómo se va haciendo.
   *
   * Es la siguiente de la lista cuya duración cabe un número entero de veces en
   * la vela: H4 no se arma con H3 (una H3 quedaría partida entre dos H4), sino
   * con H1. */
  function finer() {
    var list = charts();
    var position = list.indexOf(state.chart);
    if (position < 0) { return null; }
    for (var i = position + 1; i < list.length; i++) {
      if (barsOf(list[i]) && span(state.chart) % span(list[i]) === 0) { return list[i]; }
    }
    return null;
  }

  /* Las velas inferiores que caen dentro de la vela que se está formando, como
   * [inicio, fin) sobre el array de la temporalidad inferior. */
  function formingRange() {
    var timeframe = finer();
    var t = bars().t;
    if (!timeframe || state.cursor + 1 >= t.length) { return null; }
    var start = t[state.cursor + 1];
    var fine = barsOf(timeframe).t;
    var from = lowerBound(fine, start);
    var to = lowerBound(fine, start + span(state.chart));
    return to > from ? { timeframe: timeframe, from: from, to: to, at: start } : null;
  }

  /* Pasos intermedios que quedan dentro de la vela en curso. El último no se
   * ofrece: completar la vela es el paso que la cierra, no uno más. */
  function subSteps() {
    if (!state.forming) { return 0; }
    var edges = formingRange();
    return edges ? edges.to - edges.from - 1 : 0;
  }

  /* El reloj fino: el minuto exacto hasta el que se ha visto el mercado. Con la
   * vela en curso a medio armar no es el cierre de la última vela cerrada sino
   * el de la última vela inferior formada, y esa diferencia es justo la que hay
   * que conservar al saltar de temporalidad. */
  function clock() {
    var edges = formingRange();
    if (!state.sub || !edges) { return now_(); }
    var fine = barsOf(edges.timeframe).t;
    var last = Math.min(edges.from + state.sub, edges.to) - 1;
    return fine[last] + span(edges.timeframe);
  }

  /* La temporalidad más fina embebida por debajo de la que se mira. No es la de
   * los pasos —en el diario se avanza de H4 en H4—, sino la que da resolución al
   * dibujo de la vela en curso. */
  function finest() {
    var list = charts();
    for (var i = list.length - 1; i >= 0; i--) {
      if (barsOf(list[i]) && span(list[i]) < span(state.chart)) { return list[i]; }
    }
    return null;
  }

  /* La vela a medio hacer del borde derecho. Llega exactamente hasta el reloj y
   * se arma con la temporalidad más fina que haya, no con la de los pasos: en el
   * diario cada paso es una H4, pero si el reloj lleva hora y cuarto corrida el
   * día tiene que verse con esa hora y cuarto dentro. */
  function formingCandle() {
    if (!state.replay || !state.forming) { return null; }
    var t = bars().t;
    if (state.cursor + 1 >= t.length) { return null; }
    var start = t[state.cursor + 1];
    var timeframe = finest();
    if (!timeframe || state.at <= start) { return null; }
    var fine = barsOf(timeframe);
    var from = lowerBound(fine.t, start);
    var end = Math.min(
      lowerBound(fine.t, state.at - span(timeframe) + 1),  // cerradas al reloj
      lowerBound(fine.t, start + span(state.chart))        // y dentro de la vela
    );
    if (end <= from) { return null; }
    var high = -Infinity, low = Infinity;
    for (var i = from; i < end; i++) {
      if (fine.h[i] > high) { high = fine.h[i]; }
      if (fine.l[i] < low) { low = fine.l[i]; }
    }
    // El recuento que se enseña es el de los PASOS, que es lo que se controla
    // con ▶▶; el dibujo va más fino cuando el reloj lo permite.
    var steps = formingRange();
    return {
      x: start, o: fine.o[from], h: high, l: low, c: fine.c[end - 1],
      done: state.sub, total: steps ? steps.to - steps.from : 0,
      timeframe: steps ? steps.timeframe : timeframe,
      until: fine.t[end - 1] + span(timeframe)
    };
  }

  function formingTrace(half) {
    var rising = half.c >= half.o;
    var colour = rising ? COLORS.bullish : COLORS.bearish;
    return {
      type: "candlestick", name: "Vela en formación",
      x: [iso(half.x)], open: [half.o], high: [half.h], low: [half.l], close: [half.c],
      increasing: { line: { color: colour, width: 1.4 }, fillcolor: "rgba(0,0,0,0)" },
      decreasing: { line: { color: colour, width: 1.4 }, fillcolor: "rgba(0,0,0,0)" },
      opacity: 0.85,
      text: ["VELA EN FORMACIÓN · todavía no ha cerrado<br>" +
        stamp(half.x) + " → " + label(state.chart) +
        "<br>" + half.done + " de " + half.total + " velas de " + label(half.timeframe) +
        " · precio hasta " + iso(half.until).slice(0, 16) + " UTC" +
        "<br>O " + price(half.o) + " · H " + price(half.h) +
        "<br>L " + price(half.l) + " · C " + price(half.c)],
      hoverinfo: "text", hoverlabel: { align: "left" }
    };
  }

  // --- Zoom -------------------------------------------------------------------

  /* Plotly devuelve las marcas del eje de fechas como texto sin zona (las mismas
   * cadenas que se le dieron, que son UTC) o como milisegundos. Se vuelven a
   * minutos para poder compararlas con la ventana. */
  function toMinute(value) {
    if (value === null || value === undefined) { return null; }
    if (typeof value === "number") { return Math.round(value / 60000); }
    var text = String(value).trim().replace(" ", "T");
    if (!/(Z|[+-]\d{2}:?\d{2})$/.test(text)) { text += "Z"; }
    var ms = Date.parse(text);
    return isNaN(ms) ? null : Math.round(ms / 60000);
  }

  function axisRange(event, axis) {
    var pair = event[axis + ".range"];
    var lo = event[axis + ".range[0]"];
    var hi = event[axis + ".range[1]"];
    if (lo === undefined && pair) { lo = pair[0]; hi = pair[1]; }
    return [lo, hi];
  }

  /* Lo que el usuario acaba de hacer con la rueda, arrastrando o con el doble
   * clic. Sólo se guarda el encuadre; el gráfico ya está pintado como él quiere
   * y volver a dibujarlo aquí pelearía con su gesto. El doble clic (autorange)
   * es la salida: suelta el encuadre y devuelve el mando al replay. */
  function captureZoom(event) {
    if (!event) { return; }
    var released = false;
    ["xaxis", "yaxis"].forEach(function (axis) {
      var key = axis === "xaxis" ? "x" : "y";
      if (event[axis + ".autorange"]) { state.zoom[key] = null; released = true; return; }
      var pair = axisRange(event, axis);
      var lo = key === "x" ? toMinute(pair[0]) : Number(pair[0]);
      var hi = key === "x" ? toMinute(pair[1]) : Number(pair[1]);
      if (lo === null || hi === null || isNaN(lo) || isNaN(hi) || hi <= lo) { return; }
      state.zoom[key] = [lo, hi];
    });
    if (released) { draw(); }
  }

  function releaseZoom() {
    if (!state.zoom.x && !state.zoom.y) { return; }
    state.zoom = { x: null, y: null };
    draw();
  }

  /* --- Escalar arrastrando sobre los ejes ------------------------------------
   *
   * Como en cualquier gráfico de trading: se aprieta sobre los PRECIOS y se
   * arrastra para comprimir o estirar la vertical, y se aprieta sobre las
   * FECHAS para abrir o cerrar el gráfico de lado. Plotly, sobre el eje, hace
   * pan y no escala, así que el gesto se implementa aquí: se lee el rango
   * vigente, se multiplica por un factor y se le devuelve con `relayout`. El
   * `plotly_relayout` que eso emite lo recoge `captureZoom`, de modo que el
   * encuadre así tomado aguanta los pasos del replay igual que el de la rueda. */
  var MARGIN = { l: 66, r: 18, t: 16, b: 44 };

  var axisDrag = null;

  /* Sobre qué eje cae el punto: la banda de la izquierda es la de los precios y
   * la de abajo la de las fechas. Fuera de las dos, no es este gesto. */
  function axisAt(box, x, y) {
    if (x - box.left < MARGIN.l) { return "y"; }
    if (box.top + box.height - y < MARGIN.b) { return "x"; }
    return null;
  }

  /* El rango que se está viendo. Manda el encuadre manual si lo hay; si no, se
   * lee del gráfico ya resuelto por Plotly. */
  function currentRange(key) {
    if (state.zoom[key]) { return state.zoom[key].slice(); }
    var chart = document.getElementById("chart");
    var full = chart && (chart._fullLayout || chart.layout);
    var axis = full && full[key === "x" ? "xaxis" : "yaxis"];
    if (!axis || !axis.range) { return null; }
    var lo = key === "x" ? toMinute(axis.range[0]) : Number(axis.range[0]);
    var hi = key === "x" ? toMinute(axis.range[1]) : Number(axis.range[1]);
    if (lo === null || hi === null || isNaN(lo) || isNaN(hi) || hi <= lo) { return null; }
    return [lo, hi];
  }

  /* El rango multiplicado por `factor` alrededor de `anchor`: 0,5 es el centro y
   * 1 el extremo alto. */
  function scaledRange(pair, factor, anchor) {
    var width = pair[1] - pair[0];
    var pivot = pair[0] + width * anchor;
    var next = width * factor;
    return [pivot - next * anchor, pivot + next * (1 - anchor)];
  }

  function startAxisDrag(event) {
    var chart = document.getElementById("chart");
    if (!chart || typeof chart.getBoundingClientRect !== "function") { return; }
    var box = chart.getBoundingClientRect();
    var key = axisAt(box, event.clientX, event.clientY);
    if (!key) { return; }
    var pair = currentRange(key);
    if (!pair) { return; }
    axisDrag = { key: key, from: key === "y" ? event.clientY : event.clientX, base: pair };
    // En captura y cortando la propagación: si el evento llegara a las capas de
    // arrastre de Plotly, su pan y esta escala pelearían por el mismo gesto.
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  /* 150 px de arrastre duplican o parten en dos lo que se ve. El eje de precios
   * escala alrededor del centro; el de fechas ancla en el borde derecho, que es
   * donde está la última vela: abrir y cerrar el gráfico de lado no puede mover
   * el presente de sitio. */
  function moveAxisDrag(event) {
    if (!axisDrag) { return; }
    var delta = axisDrag.key === "y"
      ? event.clientY - axisDrag.from
      : axisDrag.from - event.clientX;
    var pair = scaledRange(
      axisDrag.base, Math.exp(delta / 150), axisDrag.key === "y" ? 0.5 : 1
    );
    applyAxisRange(axisDrag.key, pair);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applyAxisRange(key, pair) {
    var chart = document.getElementById("chart");
    if (!chart || typeof Plotly === "undefined" || !Plotly.relayout) { return; }
    var name = key === "x" ? "xaxis" : "yaxis";
    var update = {};
    update[name + ".range"] = key === "x"
      ? [iso(Math.round(pair[0])), iso(Math.round(pair[1]))]
      : pair;
    update[name + ".autorange"] = false;
    Plotly.relayout(chart, update);
  }

  function endAxisDrag() { axisDrag = null; }

  function bindAxisScaling() {
    var chart = document.getElementById("chart");
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("mousedown", startAxisDrag, true);
    document.addEventListener("mousemove", moveAxisDrag);
    document.addEventListener("mouseup", endAxisDrag);
  }

  /* --- Simulador de entradas -------------------------------------------------
   *
   * Dos botones —Largo y Corto— y la caja de siempre: el rectángulo del OBJETIVO
   * pegado por encima de la entrada y el del RIESGO por debajo, al revés en
   * corto. El botón ARMA y el clic siguiente sobre el gráfico PLANTA la caja con
   * la entrada en el precio y el minuto de ese clic. Después se arrastra: el
   * borde de fuera de cada caja mueve el objetivo o el stop, la línea de la
   * entrada mueve el conjunto entero, los bordes de los lados alargan el tramo y
   * el interior lo desplaza todo.
   *
   * ES DIBUJO A MANO Y NADA MÁS. No hay orden, ni ejecución, ni resultado: la
   * caja no lee una sola vela y nadie comprueba si el precio llegó al objetivo o
   * al stop. Sirve para medir a ojo —cuántos pips de riesgo, qué R:R— encima de
   * las velas.
   *
   * El arrastre está escrito a mano, con el mismo gesto que la escala de los
   * ejes, en vez de con las formas editables de Plotly: `edits.shapePosition`
   * las vuelve arrastrables TODAS. */
  var SIM_SIDES = [
    {
      id: "long", label: "Largo",
      title: "Arma la caja de un LARGO: objetivo por encima de la entrada, riesgo\n" +
        "por debajo. El clic siguiente sobre el gráfico la planta ahí. Es dibujo:\n" +
        "no abre nada."
    },
    {
      id: "short", label: "Corto",
      title: "Arma la caja de un CORTO: objetivo por debajo de la entrada, riesgo\n" +
        "por encima. El clic siguiente sobre el gráfico la planta ahí. Es dibujo:\n" +
        "no abre nada."
    }
  ];

  /* A cuántos riesgos NACE el objetivo de una caja recién plantada. No es un
   * R:R elegido ni un candado: es de dónde parte el arrastre, porque la caja
   * tiene que salir con algo dibujado. Desde el primer arrastre el R:R es
   * SIEMPRE el que se mida entre el stop y el objetivo que hay puestos. */
  var SIM_START_REWARD = 2;

  var GRAB = 9;          // píxeles de tolerancia para agarrar un borde

  /* Dónde empiezan las tres formas de la caja dentro de `layout.shapes`. Se fija
   * al dibujar y es lo que permite mover sólo esas tres durante el arrastre. */
  var simIndex = null;

  var simDrag = null;

  var SIM_CURSORS = {
    stop: "ns-resize", target: "ns-resize", entry: "ns-resize",
    from: "ew-resize", to: "ew-resize", body: "move"
  };

  function sideLabel(id) {
    var side = SIM_SIDES.filter(function (item) { return item.id === id; })[0];
    return side ? side.label : id;
  }

  function round_(value) { return Number(value.toFixed(decimals())); }

  function pips(distance) { return Math.round(Math.abs(distance) / pip()); }

  function decimal(value) { return value.toFixed(1).replace(".", ","); }

  /* El R:R tal y como sale de la caja: dos decimales como mucho y sin ceros de
   * relleno. Lo que se dibuja a ojo casi nunca cae en un número redondo, así que
   * 1:3 se lee «1:3» y un objetivo arrastrado un poco más allá, «1:2,25». El
   * número SIEMPRE sale de medir la caja: ningún botón lo escribe. */
  function ratioLabel(value) {
    var text = value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    return "1:" + text.replace(".", ",");
  }

  function simRisk() { return Math.abs(state.sim.entry - state.sim.stop); }

  function simReward() { return Math.abs(state.sim.target - state.sim.entry); }

  function simRatio() {
    var risk = simRisk();
    return risk > 0 ? simReward() / risk : 0;
  }

  function simBox(name, x0, x1, from, to, colour, text, position) {
    return {
      type: "rect", name: name, xref: "x", yref: "y",
      x0: x0, x1: x1, y0: from, y1: to,
      fillcolor: rgba(colour, 0.22), line: { color: rgba(colour, 0.55), width: 1 },
      layer: "above",
      label: { text: text, textposition: position, font: { size: 11, color: colour } }
    };
  }

  /* Las tres formas, siempre en este orden: objetivo, riesgo y la línea de la
   * entrada. El orden es el que usa el arrastre para saber qué está moviendo. */
  function simShapes() {
    var sim = state.sim;
    if (!sim) { return []; }
    var long_ = sim.side === "long";
    var x0 = iso(sim.from), x1 = iso(sim.to);
    // Cada rectángulo dice también lo que se juega con el capital puesto: el
    // dinero es la razón de dibujar la caja y tenerlo que buscar arriba, en la
    // barra, mientras se arrastra abajo es no verlo.
    var stake = simStake();
    return [
      simBox("sim-objetivo", x0, x1, sim.entry, sim.target, COLORS.bullish,
        "objetivo " + pips(simReward()) + " pips · " + signedMoney(stake.reward),
        long_ ? "top left" : "bottom left"),
      simBox("sim-riesgo", x0, x1, sim.entry, sim.stop, COLORS.bearish,
        "riesgo " + pips(simRisk()) + " pips · " + signedMoney(-stake.risk),
        long_ ? "bottom left" : "top left"),
      {
        type: "line", name: "sim-entrada", xref: "x", yref: "y",
        x0: x0, x1: x1, y0: sim.entry, y1: sim.entry,
        line: { color: COLORS.ink, width: 1.2 }, layer: "above",
        label: {
          text: sideLabel(sim.side).toUpperCase() + " · R:R " + ratioLabel(simRatio()),
          textposition: "end", font: { size: 11, color: COLORS.ink }
        }
      }
    ];
  }

  function armSim(side) {
    state.arming = state.arming === side ? null : side;
    draw();
  }

  /* El botón que está armado, cuando es de los del simulador: `arming` lo
   * comparten la caja simulada, los recuadros y las líneas —no se puede estar
   * esperando dos clics a la vez— y los controles de cada uno sólo pueden hablar
   * del suyo. */
  function simArming() {
    return armedRect() || armedLine() ? null : state.arming;
  }

  function clearSim() {
    state.sim = null;
    if (simArming()) { state.arming = null; }
    draw();
  }

  /* La caja de salida: el riesgo es un 5 % de lo que se ve de alto y el objetivo
   * el doble, sobre un cuarto de la ventana de ancho. Es un punto de partida
   * para arrastrar, no una propuesta. */
  function plantSim(side, point) {
    var y = viewRange("y"), x = viewRange("x");
    if (!y || !x) { return; }
    var risk = (y[1] - y[0]) * 0.05;
    var dir = side === "long" ? 1 : -1;
    state.sim = {
      side: side,
      entry: round_(point.price),
      stop: round_(point.price - dir * risk),
      target: round_(point.price + SIM_START_REWARD * dir * risk),
      from: point.minute,
      to: point.minute + Math.max(1, Math.round((x[1] - x[0]) * 0.25))
    };
    state.arming = null;
    draw();
  }

  /* La caja no puede darse la vuelta: en largo el stop va por debajo de la
   * entrada y el objetivo por encima, y al revés en corto. Un arrastre que cruce
   * la entrada se queda a un pip, que es lo que impide un R:R negativo o
   * infinito. */
  function normalizeSim() {
    var sim = state.sim;
    if (!sim) { return; }
    var dir = sim.side === "long" ? 1 : -1;
    var unit = pip();
    if (dir * (sim.entry - sim.stop) < unit) {
      sim.stop = round_(sim.entry - dir * unit);
    }
    if (dir * (sim.target - sim.entry) < unit) {
      sim.target = round_(sim.entry + dir * unit);
    }
    if (sim.to <= sim.from) {
      if (simDrag && simDrag.part === "from") { sim.from = sim.to - span(state.chart); }
      else { sim.to = sim.from + span(state.chart); }
    }
  }

  // --- El gráfico en píxeles ---------------------------------------------------

  function chartBox() {
    var chart = document.getElementById("chart");
    if (!chart || typeof chart.getBoundingClientRect !== "function") { return null; }
    return chart.getBoundingClientRect();
  }

  /* El rectángulo de dibujo dentro del div, en píxeles.
   *
   * Tiene que ser el que Plotly ha calculado, no el que se le pidió: `MARGIN` es
   * una PETICIÓN y Plotly la ensancha por su cuenta —la leyenda horizontal de
   * arriba empuja el margen superior, y las etiquetas largas del eje de precios
   * el de la izquierda—. Con el margen pedido, precio y píxel se desplazan unos
   * cuantos píxeles y los tiradores de la caja dejan de estar donde se ve la
   * línea: agarrar el stop se vuelve cuestión de suerte. */
  function plotBox(box) {
    var chart = document.getElementById("chart");
    var size = chart && chart._fullLayout && chart._fullLayout._size;
    if (size && size.w > 0 && size.h > 0) {
      return { left: box.left + size.l, top: box.top + size.t, width: size.w, height: size.h };
    }
    return {
      left: box.left + MARGIN.l, top: box.top + MARGIN.t,
      width: box.width - MARGIN.l - MARGIN.r,
      height: box.height - MARGIN.t - MARGIN.b
    };
  }

  /* Lo que se está viendo, en datos. Manda el eje ya resuelto por Plotly; si
   * todavía no hay figura de la que leerlo, se cae al tramo recortado, que es lo
   * que ese eje va a autoescalar. Los dos ejes son lineales —el de fechas
   * también, en minutos—, así que pasar de píxel a dato es una regla de tres. */
  function viewRange(key) {
    var pair = currentRange(key);
    if (pair) { return pair; }
    var cut = slice(bounds());
    var b = bars();
    if (cut.end <= cut.start) { return null; }
    if (key === "x") { return [b.t[cut.start], b.t[cut.end - 1] + span(state.chart)]; }
    var lo = Infinity, hi = -Infinity;
    for (var i = cut.start; i < cut.end; i += 1) {
      if (b.l[i] < lo) { lo = b.l[i]; }
      if (b.h[i] > hi) { hi = b.h[i]; }
    }
    return hi > lo ? [lo, hi] : null;
  }

  /* `loose` deja pasar los puntos de fuera del área de dibujo: al arrastrar, el
   * ratón se sale del gráfico y el gesto no puede morirse ahí. */
  function dataAt(box, clientX, clientY, loose) {
    var x = viewRange("x"), y = viewRange("y"), plot = plotBox(box);
    if (!x || !y || plot.width <= 0 || plot.height <= 0) { return null; }
    var fx = (clientX - plot.left) / plot.width;
    var fy = (clientY - plot.top) / plot.height;
    if (isNaN(fx) || isNaN(fy)) { return null; }
    if (!loose && (fx < 0 || fx > 1 || fy < 0 || fy > 1)) { return null; }
    return {
      minute: Math.round(x[0] + fx * (x[1] - x[0])),
      price: y[1] - fy * (y[1] - y[0])
    };
  }

  function pixelAt(box, minute, value) {
    var x = viewRange("x"), y = viewRange("y"), plot = plotBox(box);
    if (!x || !y || plot.width <= 0 || plot.height <= 0) { return null; }
    return {
      x: plot.left + (minute - x[0]) / (x[1] - x[0]) * plot.width,
      y: plot.top + (y[1] - value) / (y[1] - y[0]) * plot.height
    };
  }

  // --- Plantar y arrastrar -----------------------------------------------------

  function clickChart(event) {
    if (!state.arming) { return; }
    var box = chartBox();
    if (!box || axisAt(box, event.clientX, event.clientY)) { return; }
    var point = dataAt(box, event.clientX, event.clientY);
    if (!point) { return; }
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
    var armed = armedRect();
    if (armed) { plantRect(armed, point); return; }
    var traced = armedLine();
    if (traced) { plantLine(traced, point); return; }
    plantSim(state.arming, point);
  }

  /* Qué parte de la caja hay bajo el ratón. Las tres líneas ganan al interior:
   * con la caja estrecha, todo el rectángulo cae dentro de la tolerancia y lo
   * que se quiere agarrar entonces es el nivel más cercano. */
  function simHandleAt(box, cx, cy) {
    var sim = state.sim;
    if (!sim) { return null; }
    var left = pixelAt(box, sim.from, sim.entry);
    var right = pixelAt(box, sim.to, sim.entry);
    var stop = pixelAt(box, sim.from, sim.stop);
    var target = pixelAt(box, sim.from, sim.target);
    if (!left || !right || !stop || !target) { return null; }
    if (cx < left.x - GRAB || cx > right.x + GRAB) { return null; }
    var top = Math.min(stop.y, target.y), bottom = Math.max(stop.y, target.y);
    if (cy < top - GRAB || cy > bottom + GRAB) { return null; }
    var near = [["stop", stop.y], ["target", target.y], ["entry", left.y]]
      .filter(function (level) { return Math.abs(cy - level[1]) <= GRAB; })
      .sort(function (a, b) { return Math.abs(cy - a[1]) - Math.abs(cy - b[1]); })[0];
    if (near) { return near[0]; }
    if (Math.abs(cx - left.x) <= GRAB) { return "from"; }
    if (Math.abs(cx - right.x) <= GRAB) { return "to"; }
    return "body";
  }

  function startSimDrag(event) {
    // El gesto de los ejes se registra antes y corta la propagación cuando es
    // suyo, pero eso no impide que este oyente del mismo div se ejecute.
    if (axisDrag || state.arming || !state.sim) { return; }
    var box = chartBox();
    if (!box) { return; }
    var part = simHandleAt(box, event.clientX, event.clientY);
    if (!part) { return; }
    var origin = dataAt(box, event.clientX, event.clientY, true);
    if (!origin) { return; }
    simDrag = {
      part: part, origin: origin,
      base: {
        entry: state.sim.entry, stop: state.sim.stop, target: state.sim.target,
        from: state.sim.from, to: state.sim.to
      }
    };
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  function moveSimDrag(event) {
    if (!simDrag) { hoverSim(event); return; }
    var box = chartBox();
    var point = box && dataAt(box, event.clientX, event.clientY, true);
    if (!point) { return; }
    applySimDrag(point);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applySimDrag(point) {
    var sim = state.sim, base = simDrag.base, part = simDrag.part;
    var dy = point.price - simDrag.origin.price;
    var dx = point.minute - simDrag.origin.minute;
    if (part === "stop") {
      // El stop es una decisión sola: mueve el riesgo y deja el objetivo donde
      // está. El R:R no se defiende —se vuelve a medir— y por eso alejar el
      // stop lo baja: es lo que ha pasado en el dibujo.
      sim.stop = round_(point.price);
    } else if (part === "target") {
      sim.target = round_(point.price);
    } else if (part === "from") {
      sim.from = point.minute;
    } else if (part === "to") {
      sim.to = point.minute;
    } else {
      // La línea de la entrada y el interior mueven la caja ENTERA: las
      // distancias al stop y al objetivo son lo que se acaba de decidir y
      // recolocar la entrada no puede cambiarlas por su cuenta.
      sim.entry = round_(base.entry + dy);
      sim.stop = round_(base.stop + dy);
      sim.target = round_(base.target + dy);
      if (part === "body") { sim.from = base.from + dx; sim.to = base.to + dx; }
    }
    normalizeSim();
    redrawSim();
  }

  /* Durante el arrastre se mueven sólo las tres formas, no la figura entera: con
   * años de velas embebidas, rehacerla en cada píxel del gesto se nota. Al
   * soltar se redibuja de verdad, que es cuando se ponen al día las notas. */
  function redrawSim() {
    var chart = document.getElementById("chart");
    if (simIndex === null || !chart || typeof Plotly === "undefined" || !Plotly.relayout) {
      draw();
      return;
    }
    syncRatioReadout();
    var update = {};
    simShapes().forEach(function (shape, position) {
      var key = "shapes[" + (simIndex + position) + "]";
      update[key + ".x0"] = shape.x0;
      update[key + ".x1"] = shape.x1;
      update[key + ".y0"] = shape.y0;
      update[key + ".y1"] = shape.y1;
      update[key + ".label.text"] = shape.label.text;
    });
    Plotly.relayout(chart, update);
  }

  function endSimDrag() {
    if (!simDrag) { return; }
    simDrag = null;
    draw();
  }

  /* Sin cursor no se ve que la caja se puede agarrar: el borde de un rectángulo
   * translúcido no dice por sí solo que sea un tirador. */
  function hoverSim(event) {
    var chart = document.getElementById("chart");
    if (!chart || !chart.style || state.arming) { return; }
    if (!state.sim && !state.rects.length && !state.lines.length) { return; }
    var box = chartBox();
    var part = box && state.sim ? simHandleAt(box, event.clientX, event.clientY) : null;
    if (part) { chart.style.cursor = SIM_CURSORS[part] || ""; return; }
    var hit = box && state.rects.length
      ? rectHandleAt(box, event.clientX, event.clientY)
      : null;
    if (hit) { chart.style.cursor = RECT_CURSORS[hit.part] || ""; return; }
    var stroke = box && state.lines.length
      ? lineHandleAt(box, event.clientX, event.clientY)
      : null;
    chart.style.cursor = stroke ? (LINE_CURSORS[stroke.part] || "") : "";
  }

  /* Qué caja hay puesta. Un rectángulo de colores sobre el precio se lee como
   * una operación: el estado tiene que decir que no lo es. */
  function simCaption() {
    if (simArming()) {
      return "SIMULADOR ARMADO (" + sideLabel(simArming()).toLowerCase() +
        "): pulsa sobre el gráfico para plantar la entrada, o Escape para dejarlo";
    }
    if (!state.sim) { return null; }
    var sim = state.sim;
    return "simulación " + sideLabel(sim.side).toUpperCase() + " sobre " + sym().label +
      " · entrada " + price(sim.entry) + " · stop " + price(sim.stop) + " (" +
      pips(simRisk()) + " pips) · objetivo " + price(sim.target) + " (" +
      pips(simReward()) + " pips) · R:R " + ratioLabel(simRatio()) +
      " (automático: es la distancia que hay dibujada del stop al objetivo, y" +
      " se vuelve a medir en cuanto se arrastra cualquiera de los dos)" +
      " · un pip es la última cifra del precio de este par · ES DIBUJO A MANO: " +
      "no hay orden, ni ejecución, ni resultado; nadie mira si el precio llegó";
  }

  function buildSimButtons() {
    var container = document.getElementById("sim-buttons");
    if (!container) { return; }
    SIM_SIDES.forEach(function (side) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = side.label;
      button.dataset.side = side.id;
      button.title = side.title;
      button.addEventListener("click", function () { armSim(side.id); });
      container.appendChild(button);
    });
  }

  /* El R:R que hay DIBUJADO, en el panel y no sólo dentro del gráfico.
   *
   * NO SE ELIGE: se mide. Entrada -> stop es el 1 y entrada -> objetivo lo que
   * salga, así que el número dice lo que hay puesto sin que nadie lo escriba. Se
   * rehace en cada píxel del arrastre. */
  function syncRatioReadout() {
    var node = document.getElementById("sim-rr");
    if (!node) { return; }
    if (!state.sim) {
      node.textContent = "R:R —";
      node.dataset.source = "";
      node.title = "Sin caja dibujada no hay distancias que medir.";
      return;
    }
    node.textContent = "R:R " + ratioLabel(simRatio()) + " · automático";
    node.dataset.source = "auto";
    node.title = "Sale de medir la caja: " + pips(simRisk()) + " pips de riesgo contra " +
      pips(simReward()) + " pips de objetivo.\nNo hay ratio que poner a mano: " +
      "arrastrar el stop o el objetivo lo vuelve a medir.";
  }

  function bindSim() {
    var chart = document.getElementById("chart");
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("click", clickChart, true);
    chart.addEventListener("mousedown", startSimDrag, true);
    document.addEventListener("mousemove", moveSimDrag);
    document.addEventListener("mouseup", endSimDrag);
  }

  /* --- Los recuadros a mano ---------------------------------------------------
   *
   * Rectángulos que planta el PROPIETARIO encima del gráfico para señalar lo que
   * ve. No los calcula nadie: en el proyecto no hay todavía ninguna regla, y
   * mientras no la haya esto es lo único que marca una zona. Existen para poder
   * mandar una captura señalando lo que se quiere explicar.
   *
   * Los NOMBRES vienen del payload —los declara la configuración— porque el
   * vocabulario cambia con la estrategia que se esté escribiendo y renombrarlos
   * no puede exigir tocar este fichero.
   *
   * Cada nombre lleva SU COLOR y todos van punteados. Se marcan VARIOS de cada
   * nombre. El gesto es el mismo que el de la caja simulada —el botón ARMA, el
   * clic PLANTA y después se arrastra por los bordes o por dentro—, y por eso
   * comparten `arming`: no se puede estar esperando dos clics a la vez. */
  var RECT_KINDS = DATA.marks.rects.map(function (name) {
    return {
      id: name, label: name,
      title: "Arma el recuadro «" + name + "»: el clic siguiente sobre el gráfico\n" +
        "lo planta ahí. Escape desarma. Lo dibujas tú: no hay ninguna regla detrás."
    };
  });

  /* `arming` es UNO y lo comparten los botones de los recuadros, los de las
   * líneas y la caja simulada, así que el nombre viaja dentro del propio valor. */
  var RECT_ARM = "rect:";

  function armedRect() {
    var arming = state.arming;
    if (!arming || String(arming).indexOf(RECT_ARM) !== 0) { return null; }
    return String(arming).slice(RECT_ARM.length);
  }

  function rectColor(kind) { return COLORS.rects[kind]; }

  var RECT_CURSORS = {
    high: "ns-resize", low: "ns-resize",
    from: "ew-resize", to: "ew-resize", body: "move"
  };

  /* Dónde empiezan los recuadros dentro de `layout.shapes`, igual que la caja
   * simulada: es lo que permite mover sólo el que se arrastra en vez de rehacer
   * la figura entera en cada píxel del gesto. */
  var rectIndex = null;

  var rectDrag = null;

  /* Se numeran POR NOMBRE: el segundo de un nombre es «... 2» aunque entre los
   * dos se haya plantado uno de otro, porque lo que se cuenta al mirarlos es
   * cuántos hay de cada cosa. */
  function rectShapes() {
    var seen = {};
    return state.rects.map(function (rect, position) {
      var color = rectColor(rect.kind);
      seen[rect.kind] = (seen[rect.kind] || 0) + 1;
      return {
        type: "rect", name: "rect-" + position, xref: "x", yref: "y",
        x0: iso(rect.from), x1: iso(rect.to), y0: rect.low, y1: rect.high,
        fillcolor: rgba(color, 0.14),
        line: { color: color, width: 1.4, dash: "dot" },
        layer: "above",
        label: {
          text: rect.kind + " " + seen[rect.kind] + " (a mano)",
          textposition: "top left",
          font: { size: 11, color: color }
        }
      };
    });
  }

  function armRect(kind) {
    state.arming = armedRect() === kind ? null : RECT_ARM + kind;
    draw();
  }

  /* El recuadro de salida: alto un 4 % de lo que se ve y ancho un octavo de la
   * ventana, centrado en el precio del clic. Es un punto de partida para
   * arrastrar, no una propuesta. */
  function plantRect(kind, point) {
    var y = viewRange("y"), x = viewRange("x");
    if (!y || !x) { return; }
    var half = (y[1] - y[0]) * 0.02;
    var width = Math.max(span(state.chart), Math.round((x[1] - x[0]) * 0.125));
    state.rects.push({
      kind: kind,
      from: point.minute,
      to: point.minute + width,
      low: round_(point.price - half),
      high: round_(point.price + half)
    });
    state.arming = null;
    draw();
  }

  function undoRect() {
    if (!state.rects.length) { return; }
    state.rects.pop();
    draw();
  }

  function clearRects() {
    state.rects = [];
    if (armedRect()) { state.arming = null; }
    draw();
  }

  /* Un recuadro no puede darse la vuelta: el arrastre que cruza el borde
   * contrario se queda a un pip —o a una vela, en horizontal—, que es lo que
   * impide un rectángulo de altura cero o del revés. */
  function normalizeRect(rect) {
    var unit = pip();
    if (rect.high - rect.low < unit) {
      if (rectDrag && rectDrag.part === "low") { rect.low = round_(rect.high - unit); }
      else { rect.high = round_(rect.low + unit); }
    }
    if (rect.to <= rect.from) {
      if (rectDrag && rectDrag.part === "from") { rect.from = rect.to - span(state.chart); }
      else { rect.to = rect.from + span(state.chart); }
    }
  }

  /* Qué parte de un recuadro hay bajo el ratón. Los bordes ganan al interior:
   * con el recuadro estrecho, todo él cae dentro de la tolerancia y lo que se
   * quiere agarrar entonces es el borde más cercano. */
  function rectPartAt(box, rect, cx, cy) {
    var corner = pixelAt(box, rect.from, rect.high);
    var opposite = pixelAt(box, rect.to, rect.low);
    if (!corner || !opposite) { return null; }
    if (cx < corner.x - GRAB || cx > opposite.x + GRAB) { return null; }
    if (cy < corner.y - GRAB || cy > opposite.y + GRAB) { return null; }
    if (Math.abs(cy - corner.y) <= GRAB) { return "high"; }
    if (Math.abs(cy - opposite.y) <= GRAB) { return "low"; }
    if (Math.abs(cx - corner.x) <= GRAB) { return "from"; }
    if (Math.abs(cx - opposite.x) <= GRAB) { return "to"; }
    return "body";
  }

  /* El último plantado es el que está encima, así que se busca del final al
   * principio: con dos recuadros solapados se agarra el que se ve. */
  function rectHandleAt(box, cx, cy) {
    for (var index = state.rects.length - 1; index >= 0; index -= 1) {
      var part = rectPartAt(box, state.rects[index], cx, cy);
      if (part) { return { index: index, part: part }; }
    }
    return null;
  }

  function startRectDrag(event) {
    // La caja simulada se registra antes y tiene preferencia: si ella ha
    // agarrado el gesto, aquí no hay nada que hacer.
    if (simDrag || axisDrag || state.arming || !state.rects.length) { return; }
    var box = chartBox();
    if (!box) { return; }
    var hit = rectHandleAt(box, event.clientX, event.clientY);
    if (!hit) { return; }
    var origin = dataAt(box, event.clientX, event.clientY, true);
    if (!origin) { return; }
    var rect = state.rects[hit.index];
    rectDrag = {
      index: hit.index, part: hit.part, origin: origin,
      base: { from: rect.from, to: rect.to, low: rect.low, high: rect.high }
    };
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  function moveRectDrag(event) {
    if (simDrag || !rectDrag) { return; }
    var box = chartBox();
    var point = box && dataAt(box, event.clientX, event.clientY, true);
    if (!point) { return; }
    applyRectDrag(point);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applyRectDrag(point) {
    var rect = state.rects[rectDrag.index], base = rectDrag.base, part = rectDrag.part;
    if (part === "high") {
      rect.high = round_(point.price);
    } else if (part === "low") {
      rect.low = round_(point.price);
    } else if (part === "from") {
      rect.from = point.minute;
    } else if (part === "to") {
      rect.to = point.minute;
    } else {
      // Por dentro se mueve el recuadro ENTERO: su tamaño es lo que se acaba de
      // decidir y recolocarlo no puede cambiarlo por su cuenta.
      var dy = point.price - rectDrag.origin.price;
      var dx = point.minute - rectDrag.origin.minute;
      rect.low = round_(base.low + dy);
      rect.high = round_(base.high + dy);
      rect.from = base.from + dx;
      rect.to = base.to + dx;
    }
    normalizeRect(rect);
    redrawRects();
  }

  /* Igual que la caja simulada: durante el arrastre se mueven sólo las formas
   * de los recuadros, no la figura entera. */
  function redrawRects() {
    var chart = document.getElementById("chart");
    if (rectIndex === null || !chart || typeof Plotly === "undefined" || !Plotly.relayout) {
      draw();
      return;
    }
    var update = {};
    rectShapes().forEach(function (shape, position) {
      var key = "shapes[" + (rectIndex + position) + "]";
      update[key + ".x0"] = shape.x0;
      update[key + ".x1"] = shape.x1;
      update[key + ".y0"] = shape.y0;
      update[key + ".y1"] = shape.y1;
    });
    Plotly.relayout(chart, update);
  }

  function endRectDrag() {
    if (!rectDrag) { return; }
    rectDrag = null;
    draw();
  }

  /* Cuántos hay de cada nombre, en el orden de los botones y sin nombrar los
   * que no se han puesto. */
  function rectCounts() {
    var counts = {};
    state.rects.forEach(function (rect) {
      counts[rect.kind] = (counts[rect.kind] || 0) + 1;
    });
    return RECT_KINDS.filter(function (kind) {
      return counts[kind.id];
    }).map(function (kind) {
      return counts[kind.id] + " de «" + kind.id + "»";
    }).join(" · ");
  }

  /* Qué recuadros hay puestos. Un rectángulo encima del precio se lee como algo
   * que ha encontrado un motor: el estado tiene que decir que lo ha puesto una
   * mano y que detrás no hay ninguna regla. */
  function rectCaption() {
    var armed = armedRect();
    if (armed) {
      return "RECUADRO «" + armed + "» ARMADO: pulsa sobre el gráfico para " +
        "plantarlo, o Escape para dejarlo";
    }
    if (!state.rects.length) { return null; }
    return "recuadros marcados a mano en " + sym().label + ": " + state.rects.length +
      " (" + rectCounts() + ")" +
      " · los dibuja el propietario: NO los ha detectado ningún motor, en el " +
      "proyecto no hay todavía ninguna regla y no salen de la pantalla";
  }

  function buildRectButtons() {
    var container = document.getElementById("rect-buttons");
    if (!container) { return; }
    RECT_KINDS.forEach(function (kind) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = kind.label;
      button.dataset.kind = kind.id;
      button.title = kind.title;
      button.addEventListener("click", function () { armRect(kind.id); });
      container.appendChild(button);
    });
  }

  function bindRects() {
    var chart = document.getElementById("chart");
    document.getElementById("rect-undo").addEventListener("click", undoRect);
    document.getElementById("rect-clear").addEventListener("click", clearRects);
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("mousedown", startRectDrag, true);
    document.addEventListener("mousemove", moveRectDrag);
    document.addEventListener("mouseup", endRectDrag);
  }

  /* --- Las líneas a mano ------------------------------------------------------
   *
   * LÍNEAS que traza el PROPIETARIO encima del gráfico para señalar por dónde
   * pasa un nivel, qué dos puntos une o de dónde a dónde mira. Como los
   * recuadros, no las calcula nadie y no salen de la pantalla. Lo que las separa
   * de un recuadro es el TRAZO: continuo la línea, punteado el recuadro.
   *
   * Los nombres vienen del payload, igual que los de los recuadros.
   *
   * Se plantan HORIZONTALES —marcar un nivel es lo que más se hace— y se
   * inclinan arrastrando un extremo: los dos se mueven en precio y en tiempo, y
   * por dentro se desplaza entera. */
  var LINE_KINDS = DATA.marks.lines.map(function (name) {
    return { id: name, label: name, name: "línea «" + name + "»" };
  });

  var LINE_ARM = "line:";

  function armedLine() {
    var arming = state.arming;
    if (!arming || String(arming).indexOf(LINE_ARM) !== 0) { return null; }
    return String(arming).slice(LINE_ARM.length);
  }

  function lineColor(kind) { return COLORS.lines[kind]; }

  function lineName(kind) {
    var found = LINE_KINDS.filter(function (item) { return item.id === kind; })[0];
    return found ? found.name : kind;
  }

  var LINE_CURSORS = { left: "ew-resize", right: "ew-resize", body: "move" };

  /* Dónde empiezan las líneas dentro de `layout.shapes`, igual que los
   * recuadros: es lo que permite mover sólo la que se arrastra. */
  var lineIndex = null;

  var lineDrag = null;

  /* Se numeran POR NOMBRE: la segunda de un nombre es «... 2» aunque entre las
   * dos se haya trazado una de otro. */
  function lineShapes() {
    var seen = {};
    return state.lines.map(function (line, position) {
      var color = lineColor(line.kind);
      seen[line.kind] = (seen[line.kind] || 0) + 1;
      return {
        type: "line", name: "line-" + position, xref: "x", yref: "y",
        x0: iso(line.from), x1: iso(line.to), y0: line.left, y1: line.right,
        line: { color: color, width: 2.2 },
        layer: "above",
        label: {
          text: line.kind + " " + seen[line.kind] + " (a mano)",
          textposition: "top left",
          font: { size: 11, color: color }
        }
      };
    });
  }

  function armLine(kind) {
    state.arming = armedLine() === kind ? null : LINE_ARM + kind;
    draw();
  }

  /* La línea de salida: HORIZONTAL al precio del clic y de media ventana de
   * ancho, centrada en él. Es un punto de partida para arrastrar. */
  function plantLine(kind, point) {
    var x = viewRange("x");
    if (!x) { return; }
    var half = Math.max(span(state.chart), Math.round((x[1] - x[0]) * 0.25));
    var level = round_(point.price);
    state.lines.push({
      kind: kind,
      from: point.minute - half,
      to: point.minute + half,
      left: level,
      right: level
    });
    state.arming = null;
    draw();
  }

  function undoLine() {
    if (!state.lines.length) { return; }
    state.lines.pop();
    draw();
  }

  function clearLines() {
    state.lines = [];
    if (armedLine()) { state.arming = null; }
    draw();
  }

  /* Una línea no puede darse la vuelta: el arrastre que cruza el otro extremo se
   * queda a una vela, que es lo que impide un segmento de ancho cero o del
   * revés. En vertical no hay nada que impedir: horizontal es justo como nace. */
  function normalizeLine(line) {
    if (line.to <= line.from) {
      if (lineDrag && lineDrag.part === "left") {
        line.from = line.to - span(state.chart);
      } else {
        line.to = line.from + span(state.chart);
      }
    }
  }

  /* Distancia del ratón al trazo, en píxeles: su proyección sobre el segmento,
   * recortada a los extremos. Sin esto una línea inclinada sólo se dejaría
   * agarrar en el punto donde coincide con el ratón en horizontal. */
  function strokeDistance(cx, cy, a, b) {
    var dx = b.x - a.x, dy = b.y - a.y;
    var length = dx * dx + dy * dy;
    var t = length ? ((cx - a.x) * dx + (cy - a.y) * dy) / length : 0;
    t = Math.max(0, Math.min(1, t));
    var x = a.x + t * dx, y = a.y + t * dy;
    return Math.sqrt((cx - x) * (cx - x) + (cy - y) * (cy - y));
  }

  function nearPixel(cx, cy, point) {
    return Math.abs(cx - point.x) <= GRAB && Math.abs(cy - point.y) <= GRAB;
  }

  /* Qué parte de una línea hay bajo el ratón. Los extremos ganan al trazo: son
   * lo que la inclina, y con la línea corta todo ella cae dentro de la
   * tolerancia de los dos. */
  function linePartAt(box, line, cx, cy) {
    var a = pixelAt(box, line.from, line.left);
    var b = pixelAt(box, line.to, line.right);
    if (!a || !b) { return null; }
    if (nearPixel(cx, cy, a)) { return "left"; }
    if (nearPixel(cx, cy, b)) { return "right"; }
    return strokeDistance(cx, cy, a, b) <= GRAB ? "body" : null;
  }

  /* La última trazada está encima, así que se busca del final al principio. */
  function lineHandleAt(box, cx, cy) {
    for (var index = state.lines.length - 1; index >= 0; index -= 1) {
      var part = linePartAt(box, state.lines[index], cx, cy);
      if (part) { return { index: index, part: part }; }
    }
    return null;
  }

  function startLineDrag(event) {
    // La caja simulada y los recuadros se registran antes y tienen preferencia:
    // si uno de ellos ha agarrado el gesto, aquí no hay nada que hacer.
    if (simDrag || rectDrag || axisDrag || state.arming || !state.lines.length) { return; }
    var box = chartBox();
    if (!box) { return; }
    var hit = lineHandleAt(box, event.clientX, event.clientY);
    if (!hit) { return; }
    var origin = dataAt(box, event.clientX, event.clientY, true);
    if (!origin) { return; }
    var line = state.lines[hit.index];
    lineDrag = {
      index: hit.index, part: hit.part, origin: origin,
      base: { from: line.from, to: line.to, left: line.left, right: line.right }
    };
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  function moveLineDrag(event) {
    if (simDrag || rectDrag || !lineDrag) { return; }
    var box = chartBox();
    var point = box && dataAt(box, event.clientX, event.clientY, true);
    if (!point) { return; }
    applyLineDrag(point);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applyLineDrag(point) {
    var line = state.lines[lineDrag.index], base = lineDrag.base, part = lineDrag.part;
    if (part === "left") {
      line.from = point.minute;
      line.left = round_(point.price);
    } else if (part === "right") {
      line.to = point.minute;
      line.right = round_(point.price);
    } else {
      // Por dentro se mueve la línea ENTERA: su inclinación es lo que se acaba
      // de decidir y recolocarla no puede cambiarla por su cuenta.
      var dy = point.price - lineDrag.origin.price;
      var dx = point.minute - lineDrag.origin.minute;
      line.from = base.from + dx;
      line.to = base.to + dx;
      line.left = round_(base.left + dy);
      line.right = round_(base.right + dy);
    }
    normalizeLine(line);
    redrawLines();
  }

  /* Igual que los recuadros: durante el arrastre se mueven sólo las formas de
   * las líneas, no la figura entera. */
  function redrawLines() {
    var chart = document.getElementById("chart");
    if (lineIndex === null || !chart || typeof Plotly === "undefined" || !Plotly.relayout) {
      draw();
      return;
    }
    var update = {};
    lineShapes().forEach(function (shape, position) {
      var key = "shapes[" + (lineIndex + position) + "]";
      update[key + ".x0"] = shape.x0;
      update[key + ".x1"] = shape.x1;
      update[key + ".y0"] = shape.y0;
      update[key + ".y1"] = shape.y1;
    });
    Plotly.relayout(chart, update);
  }

  function endLineDrag() {
    if (!lineDrag) { return; }
    lineDrag = null;
    draw();
  }

  /* Cuántas hay de cada nombre, en el orden de los botones y sin nombrar las
   * que no se han trazado. */
  function lineCounts() {
    var counts = {};
    state.lines.forEach(function (line) {
      counts[line.kind] = (counts[line.kind] || 0) + 1;
    });
    return LINE_KINDS.filter(function (kind) {
      return counts[kind.id];
    }).map(function (kind) {
      return counts[kind.id] + " de «" + kind.label + "»";
    }).join(" · ");
  }

  /* Qué líneas hay trazadas. Un trazo sobre el precio se lee como un nivel que
   * ha encontrado alguien: el estado tiene que decir que lo ha puesto una mano. */
  function lineCaption() {
    var armed = armedLine();
    if (armed) {
      return lineName(armed).toUpperCase() + " ARMADA: pulsa sobre el gráfico " +
        "para plantarla, o Escape para dejarla";
    }
    if (!state.lines.length) { return null; }
    return "líneas marcadas a mano en " + sym().label + ": " + state.lines.length +
      " (" + lineCounts() + ")" +
      " · las traza el propietario: NO las ha dibujado ningún motor, no hay " +
      "ninguna regla detrás y no salen de la pantalla";
  }

  function buildLineButtons() {
    var container = document.getElementById("line-buttons");
    if (!container) { return; }
    LINE_KINDS.forEach(function (kind) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = kind.label;
      button.dataset.kind = kind.id;
      button.title = "Arma la " + kind.name + ": el clic siguiente sobre el\n" +
        "gráfico la planta ahí, horizontal al precio pulsado. Escape desarma.\n" +
        "La dibujas tú y no hay ninguna regla detrás.";
      button.addEventListener("click", function () { armLine(kind.id); });
      container.appendChild(button);
    });
  }

  function bindLines() {
    var chart = document.getElementById("chart");
    document.getElementById("line-undo").addEventListener("click", undoLine);
    document.getElementById("line-clear").addEventListener("click", clearLines);
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("mousedown", startLineDrag, true);
    document.addEventListener("mousemove", moveLineDrag);
    document.addEventListener("mouseup", endLineDrag);
  }

  /* --- Cuenta simulada --------------------------------------------------------
   *
   * Un capital de partida, un riesgo por operación y tres botones: la caja que
   * hay dibujada se apunta como GANADA, PERDIDA o en BREAK-EVEN y el saldo se
   * mueve. Lo que se cobra es el R:R de esa caja: ganar suma el riesgo por el
   * ratio, perder resta el riesgo entero y el break-even no mueve nada.
   *
   * SIGUE SIENDO DIBUJO. Nadie mira las velas: quien decide si esa entrada ganó
   * o perdió es el propietario, mirando el gráfico. No hay orden, no hay
   * ejecución y esto no es dinero.
   *
   * Del histórico se guarda el MÚLTIPLO DE RIESGO de cada operación (+ratio,
   * -1, 0), no los euros: el dinero se recalcula entero desde el capital de
   * partida cada vez que se dibuja. Así, cambiar el capital o el riesgo reescala
   * la curva completa en vez de dejar apuntadas cifras de una configuración que
   * ya no está puesta.
   *
   * El riesgo NO compone: el porcentaje es del capital escrito en la casilla y
   * la apuesta es la misma en todas las operaciones. Es lo que se quiere para
   * juzgar una racha; para subirla se sube el capital a mano. */
  var ACCOUNT_RESULTS = [
    {
      id: "win", label: "Ganada",
      title: "Apunta la caja dibujada como GANADA: suma el riesgo por el R:R de\n" +
        "la caja. Quita la caja del gráfico; «Deshacer» la devuelve."
    },
    {
      id: "loss", label: "Perdida",
      title: "Apunta la caja dibujada como PERDIDA: resta el riesgo entero.\n" +
        "Quita la caja del gráfico; «Deshacer» la devuelve."
    },
    {
      id: "be", label: "BE",
      title: "Break-even: la operación se apunta y no mueve el saldo. Cuenta en el\n" +
        "número de operaciones, pero no en el porcentaje de acierto."
    }
  ];

  var RESULT_NAMES = { win: "GANADA", loss: "PERDIDA", be: "BREAK-EVEN" };

  /* Cómo se mide el riesgo de la siguiente operación: un porcentaje del capital
   * de partida o una cantidad fija. */
  var RISK_MODES = [
    { id: "percent", label: "% del capital" },
    { id: "cash", label: "$ fijos" }
  ];

  function round2(value) { return Math.round(value * 100) / 100; }

  function money(value) {
    return round2(value).toLocaleString(
      "es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }
    ) + " $";
  }

  function signedMoney(value) {
    return (value > 0 ? "+" : value < 0 ? "-" : "") + money(Math.abs(round2(value)));
  }

  function pct(value) {
    return value.toLocaleString(
      "es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 }
    ) + " %";
  }

  function signedPct(value) {
    return (value > 0 ? "+" : value < 0 ? "-" : "") + pct(Math.abs(value));
  }

  function signedR(value) {
    return (value > 0 ? "+" : value < 0 ? "-" : "") + decimal(Math.abs(value)) + " R";
  }

  function plural(count, one, many) {
    return count.toLocaleString("es-ES") + " " + (count === 1 ? one : many);
  }

  /* Lo que se arriesga en cada operación. El porcentaje es SIEMPRE del capital
   * de partida —el que hay escrito en la casilla—, no del saldo vivo: 50 $ al
   * 19 % son 9,50 $ en la primera operación y en la número treinta. Para que la
   * apuesta suba con la cuenta hay que subir el capital a mano, y entonces la
   * curva se vuelve a contar entera con él. */
  function riskFor() {
    var account = state.account;
    if (account.mode === "cash") { return round2(account.risk); }
    return round2(account.initial * account.risk / 100);
  }

  /* La curva, recalculada desde el capital de partida. Cada fila lleva lo que se
   * arriesgaba en ese momento, lo que movió y el saldo con el que se quedó. */
  function accountRows() {
    var balance = state.account.initial;
    return state.account.trades.map(function (trade) {
      var risk = riskFor();
      var delta = round2(risk * trade.r);
      balance = round2(balance + delta);
      return { trade: trade, risk: risk, delta: delta, balance: balance };
    });
  }

  function accountStats() {
    var rows = accountRows();
    var initial = state.account.initial;
    var balance = rows.length ? rows[rows.length - 1].balance : initial;
    var counts = { win: 0, loss: 0, be: 0 };
    var r = 0;
    var peak = initial;
    var drawdown = 0;
    var drawdownPct = 0;
    rows.forEach(function (row) {
      counts[row.trade.result] += 1;
      r += row.trade.r;
      if (row.balance > peak) { peak = row.balance; }
      var fall = peak - row.balance;
      if (fall > drawdown) {
        drawdown = fall;
        drawdownPct = peak > 0 ? fall / peak * 100 : 0;
      }
    });
    var decided = counts.win + counts.loss;
    return {
      rows: rows, initial: initial, balance: balance, counts: counts,
      trades: rows.length, r: round2(r),
      net: round2(balance - initial),
      netPct: initial > 0 ? (balance - initial) / initial * 100 : 0,
      hit: decided ? counts.win / decided * 100 : null,
      drawdown: round2(drawdown), drawdownPct: drawdownPct,
      risk: riskFor()
    };
  }

  /* Lo que la caja dibujada se juega AHORA: lo que resta si toca el stop y lo
   * que suma si llega al objetivo. Es lo que va escrito dentro de cada
   * rectángulo, para no tener que mirar arriba mientras se dibuja abajo. */
  function simStake() {
    var risk = riskFor();
    return { risk: risk, reward: round2(risk * simRatio()) };
  }

  function recordTrade(result) {
    var sim = state.sim;
    if (!sim) { return; }
    var ratio = Number(simRatio().toFixed(2));
    state.account.copied = null;
    state.account.trades.push({
      result: result,
      // El múltiplo de riesgo: es lo único que no depende del capital puesto.
      r: result === "win" ? ratio : (result === "loss" ? -1 : 0),
      ratio: ratio,
      // Sobre qué par y en qué gráfico se dibujó: con cuatro pares en el mismo
      // fichero, una lista de operaciones sin el par no dice de qué habla.
      symbol: sym().label,
      decimals: decimals(),
      chart: state.chart,
      at: iso(sim.from),
      box: {
        side: sim.side, entry: sim.entry, stop: sim.stop, target: sim.target,
        from: sim.from, to: sim.to
      }
    });
    // La caja se va: ya está cobrada, y dejarla puesta invita a apuntarla dos
    // veces. La siguiente entrada se planta como la primera.
    state.sim = null;
    state.arming = null;
    draw();
  }

  /* Deshacer devuelve el saldo Y la caja: el error que se deshace casi siempre
   * es haber pulsado el botón que no era, y replantar el dibujo a mano para
   * volver a cobrarlo bien sería perder la medida. Sólo devuelve la caja si la
   * operación se apuntó sobre el par que se está mirando: replantar en otro una
   * caja de precios que no son los suyos la dejaría fuera de la pantalla. */
  function undoTrade() {
    var last = state.account.trades.pop();
    if (!last) { return; }
    state.account.copied = null;
    if (last.symbol === sym().label) {
      state.sim = {
        side: last.box.side, entry: last.box.entry, stop: last.box.stop,
        target: last.box.target, from: last.box.from, to: last.box.to
      };
      state.arming = null;
    }
    draw();
  }

  function resetAccount() {
    state.account.trades = [];
    state.account.copied = null;
    draw();
  }

  function setInitial(value) {
    var amount = parseFloat(value);
    // Un capital vacío o negativo no se acepta: `syncControls` repone el que
    // había, así que el control nunca se queda diciendo algo que no está puesto.
    if (isNaN(amount) || amount <= 0) { draw(); return; }
    state.account.initial = round2(amount);
    state.account.copied = null;
    draw();
  }

  function setRiskMode(value) {
    if (value !== "percent" && value !== "cash") { return; }
    state.account.mode = value;
    state.account.risk = value === "percent"
      ? Math.min(100, state.account.risk)
      : state.account.risk;
    state.account.copied = null;
    draw();
  }

  function setRisk(value) {
    var amount = parseFloat(value);
    if (isNaN(amount) || amount <= 0) { draw(); return; }
    if (state.account.mode === "percent" && amount > 100) { draw(); return; }
    state.account.risk = round2(amount);
    state.account.copied = null;
    draw();
  }

  function pad(text, width) {
    var out = String(text);
    while (out.length < width) { out += " "; }
    return out;
  }

  function riskLabel() {
    return state.account.mode === "percent"
      ? pct(state.account.risk) + " del capital de partida"
      : money(state.account.risk) + " fijos";
  }

  /* El precio de una operación apuntada, con los decimales del par sobre el que
   * se dibujó y no con los del que se está mirando ahora. */
  function tradePrice(trade, value) {
    var digits = typeof trade.decimals === "number" ? trade.decimals : decimals();
    return value.toFixed(digits);
  }

  /* El historial en texto plano, para pegarlo fuera del explorador. Lleva la
   * configuración con la que está contado: la misma lista de operaciones da otra
   * curva con otro capital o con otro riesgo. */
  function accountText() {
    var stats = accountStats();
    var lines = [
      "CUENTA SIMULADA · la apunta el propietario a mano sobre cajas dibujadas: " +
        "no hay orden ninguna detrás y nadie ha mirado si el precio llegó",
      "capital de partida " + money(stats.initial) + " · riesgo " + riskLabel() +
        " · " + money(stats.risk) + " en la siguiente operación",
      "capital " + money(stats.balance) + " · " + signedMoney(stats.net) +
        " (" + signedPct(stats.netPct) + ") · " + signedR(stats.r) +
        " · " + plural(stats.trades, "operación", "operaciones") +
        " (" + stats.counts.win + " ganadas, " + stats.counts.loss + " perdidas, " +
        stats.counts.be + " en break-even) · acierto " +
        (stats.hit === null ? "sin decidir" : pct(stats.hit)) +
        " · caída máxima " + money(stats.drawdown) + " (" + pct(stats.drawdownPct) + ")"
    ];
    if (!stats.trades) {
      lines.push("");
      lines.push("sin operaciones apuntadas");
      return lines.join("\n");
    }
    lines.push("");
    lines.push(
      pad("nº", 4) + pad("entrada (UTC)", 21) + pad("par", 10) + pad("gráfico", 9) +
      pad("lado", 7) + pad("precio", 11) + pad("stop", 11) + pad("objetivo", 12) +
      pad("R:R", 8) + pad("resultado", 12) + pad("R", 7) + pad("mueve", 12) + "capital"
    );
    stats.rows.forEach(function (row, index) {
      var trade = row.trade;
      lines.push(
        pad(index + 1, 4) + pad(trade.at.slice(0, 16), 21) +
        pad(trade.symbol || "—", 10) + pad(label(trade.chart), 9) +
        pad(sideLabel(trade.box.side).toUpperCase(), 7) +
        pad(tradePrice(trade, trade.box.entry), 11) +
        pad(tradePrice(trade, trade.box.stop), 11) +
        pad(tradePrice(trade, trade.box.target), 12) + pad(ratioLabel(trade.ratio), 8) +
        pad(RESULT_NAMES[trade.result], 12) + pad(signedR(trade.r).replace(" R", ""), 7) +
        pad(signedMoney(row.delta), 12) + money(row.balance)
      );
    });
    return lines.join("\n");
  }

  /* Copiar es de la barra, no del gráfico: el portapapeles puede no estar —un
   * navegador viejo, un fichero abierto sin permiso— y el estado tiene que
   * decirlo en vez de dejar creer que el historial ya está guardado fuera. */
  function copyAccount() {
    var text = accountText();
    var stats = accountStats();
    var done = false;
    try {
      var clipboard = typeof navigator !== "undefined" && navigator.clipboard;
      if (clipboard && clipboard.writeText) {
        var promise = clipboard.writeText(text);
        // El permiso se resuelve después: si lo niegan, el estado tiene que
        // desdecirse en vez de dejar creer que el historial ya está fuera.
        if (promise && promise.catch) {
          promise.catch(function () {
            state.account.copied = { ok: false, trades: stats.trades };
            draw();
          });
        }
        done = true;
      } else {
        done = legacyCopy(text);
      }
    } catch (error) {
      done = false;
    }
    state.account.copied = { ok: done, trades: stats.trades };
    draw();
  }

  function legacyCopy(text) {
    if (!document.body || !document.execCommand) { return false; }
    var area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    if (area.select) { area.select(); }
    var done = document.execCommand("copy");
    if (document.body.removeChild) { document.body.removeChild(area); }
    return done === true;
  }

  /* Lo corto, para la barra: el saldo tiene que verse sin leer nada más. */
  function accountSummary() {
    var stats = accountStats();
    return money(stats.balance) + " · riesgo " + money(stats.risk) + " · " +
      plural(stats.trades, "operación", "operaciones") +
      (stats.trades ? " · " + signedR(stats.r) : "");
  }

  /* Lo largo, para el estado de abajo. Sólo aparece cuando hay algo que contar
   * —una caja dibujada o alguna operación apuntada—. */
  function accountCaption() {
    var stats = accountStats();
    if (!stats.trades && !state.sim) { return null; }
    var text = "CUENTA SIMULADA: capital " + money(stats.balance) +
      " (partía de " + money(stats.initial) + ") · " + signedMoney(stats.net) +
      " (" + signedPct(stats.netPct) + ") · riesgo " + riskLabel() + " = " +
      money(stats.risk) + " por operación";
    if (stats.trades) {
      text += " · " + plural(stats.trades, "operación apuntada", "operaciones apuntadas") +
        " (" + stats.counts.win + " ganadas, " + stats.counts.loss + " perdidas, " +
        stats.counts.be + " en break-even) · acierto " +
        (stats.hit === null ? "sin decidir" : pct(stats.hit)) +
        " (el break-even no cuenta) · " + signedR(stats.r) +
        " · caída máxima " + money(stats.drawdown) + " (" + pct(stats.drawdownPct) + ")" +
        " · la cuenta es UNA para los cuatro pares: cada operación apuntada dice " +
        "sobre cuál se dibujó";
    }
    if (state.sim) {
      var stake = simStake();
      text += " · la caja dibujada se juega " + money(stake.risk) + " para ganar " +
        money(stake.reward);
    }
    if (stats.balance <= 0) {
      text += " · CUENTA A CERO: no queda capital que arriesgar y las operaciones " +
        "siguientes no mueven nada";
    }
    if (state.account.copied) {
      text += state.account.copied.ok
        ? " · historial copiado al portapapeles (" +
          plural(state.account.copied.trades, "operación", "operaciones") + ")"
        : " · NO SE HA PODIDO COPIAR: este navegador no deja escribir en el portapapeles";
    }
    return text + " · NO ES DINERO: los resultados los apunta el propietario a mano " +
      "mirando el gráfico; nadie comprueba si el precio llegó";
  }

  function buildAccountButtons() {
    var container = document.getElementById("account-buttons");
    if (!container) { return; }
    ACCOUNT_RESULTS.forEach(function (result) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = result.label;
      button.dataset.result = result.id;
      button.title = result.title;
      button.addEventListener("click", function () { recordTrade(result.id); });
      container.appendChild(button);
    });
  }

  function buildRiskModes() {
    var select = document.getElementById("account-mode");
    if (!select) { return; }
    RISK_MODES.forEach(function (mode) {
      var option = document.createElement("option");
      option.value = mode.id;
      option.textContent = mode.label;
      select.appendChild(option);
    });
    select.value = state.account.mode;
  }

  function bindAccount() {
    document.getElementById("account-initial").addEventListener("change", function (event) {
      setInitial(event.target.value);
    });
    document.getElementById("account-risk").addEventListener("change", function (event) {
      setRisk(event.target.value);
    });
    document.getElementById("account-mode").addEventListener("change", function (event) {
      setRiskMode(event.target.value);
    });
    document.getElementById("account-undo").addEventListener("click", undoTrade);
    document.getElementById("account-reset").addEventListener("click", resetAccount);
    document.getElementById("account-copy").addEventListener("click", copyAccount);
  }

  function syncAccount() {
    var stats = accountStats();
    document.getElementById("account-initial").value = String(state.account.initial);
    document.getElementById("account-risk").value = String(state.account.risk);
    document.getElementById("account-mode").value = state.account.mode;
    document.getElementById("account-summary").textContent = accountSummary();
    // Sin caja dibujada no hay nada que cobrar: lo que se apunta es SIEMPRE una
    // caja concreta, con su R:R y su fecha, no un resultado suelto.
    document.querySelectorAll("#account-buttons button").forEach(function (button) {
      button.disabled = !state.sim;
    });
    document.getElementById("account-undo").disabled = !stats.trades;
    document.getElementById("account-reset").disabled = !stats.trades;
    document.getElementById("account-copy").disabled = !stats.trades;
  }

  /* Cambiar de tramo de historia —preset, fechas, ventana ciega, arrancar o
   * salir del replay, y cambiar de PAR— es pedir otro sitio, no otro zoom: ahí
   * el encuadre manual estorba. Alternar la vista o dar un paso del replay no lo
   * tocan. */
  function dropZoom() { state.zoom = { x: null, y: null }; }

  /* Desplaza el encuadre del usuario lo justo para que el presente siga dentro,
   * conservando su anchura: el nivel de zoom es suyo, la posición la manda el
   * reloj. Mientras el borde derecho quepa, no se mueve nada. */
  function followX(view, present) {
    var air = span(state.chart);
    var edge = present + 2 * air;        // la vela en formación, más un respiro
    var width = view[1] - view[0];
    if (edge > view[1]) { return [Math.round(edge - width), Math.round(edge)]; }
    if (present < view[0]) {
      return [Math.round(present - width / 2), Math.round(present + width / 2)];
    }
    return view;
  }

  function xRange(range) {
    if (state.zoom.x) { return [iso(state.zoom.x[0]), iso(state.zoom.x[1])]; }
    // En replay el eje se fija a mano y deja aire a la derecha: si se
    // autoescalara, la última vela quedaría pegada al borde y el gráfico daría
    // un salto en cada paso.
    return state.replay
      ? [iso(range.lo), iso(range.hi + 8 * span(state.chart))]
      : undefined;
  }

  /* Se engancha una sola vez, después del primer dibujo: `Plotly.react` conserva
   * los oyentes del div. El `on` lo pone Plotly al montar el gráfico, así que si
   * todavía no está se reintenta en cuanto el navegador respire. */
  var zoomBound = false;
  var zoomTries = 0;

  function bindZoom() {
    if (zoomBound) { return; }
    var chart = document.getElementById("chart");
    if (!chart || typeof chart.on !== "function") {
      // Acotado: si el gráfico no aparece, se deja de insistir en vez de dejar
      // un temporizador dando vueltas para siempre.
      if (typeof setTimeout === "function" && zoomTries < 20) {
        zoomTries += 1;
        setTimeout(bindZoom, 50);
      }
      return;
    }
    zoomBound = true;
    chart.on("plotly_relayout", captureZoom);
  }

  // --- Figura -------------------------------------------------------------------

  /* Las tres formas de la caja simulada. Se apunta dónde empiezan: es lo que
   * permite mover sólo esas tres mientras se arrastra en vez de rehacer la
   * figura entera. */
  function simShapes_(shapes) {
    var box = simShapes();
    simIndex = box.length ? shapes.length : null;
    return shapes.concat(box);
  }

  /* Y encima, los recuadros a mano. Se apunta dónde empiezan por lo mismo que la
   * caja simulada: para poder arrastrar uno sin rehacer la figura entera. */
  function rectShapes_(shapes) {
    var boxes = rectShapes();
    rectIndex = boxes.length ? shapes.length : null;
    return shapes.concat(boxes);
  }

  /* Y por encima de los recuadros, las líneas: son lo último que se planta y lo
   * que se está señalando. */
  function lineShapes_(shapes) {
    var strokes = lineShapes();
    lineIndex = strokes.length ? shapes.length : null;
    return shapes.concat(strokes);
  }

  function layout(range) {
    var x = xRange(range);
    return {
      height: 720,
      margin: MARGIN,
      paper_bgcolor: COLORS.surface,
      plot_bgcolor: COLORS.surface,
      font: { family: COLORS.font, size: 12, color: COLORS.ink },
      hovermode: "closest",
      hoverdistance: 16,
      hoverlabel: { font: { size: 12 }, namelength: -1 },
      dragmode: "pan",
      showlegend: true,
      legend: { orientation: "h", y: 1.04, x: 0, font: { size: 11 } },
      shapes: lineShapes_(rectShapes_(simShapes_([]))),
      xaxis: {
        type: "date", gridcolor: COLORS.grid, rangeslider: { visible: false },
        // El rango va siempre con su `autorange`: si se diera uno sin apagar el
        // otro, Plotly reescalaría el eje y el encuadre no aguantaría el paso.
        range: x, autorange: x ? false : true,
        title: { text: "UTC", font: { size: 11, color: COLORS.muted } }
      },
      yaxis: {
        gridcolor: COLORS.grid, tickformat: "." + decimals() + "f", fixedrange: false,
        // Sin esto el eje de precios se rehace en cada paso y el gráfico "salta"
        // en vertical: con encuadre manual manda lo que fijó el propietario.
        range: state.zoom.y || undefined,
        autorange: state.zoom.y ? false : true
      }
    };
  }

  function draw() {
    // El encuadre manual sigue al reloj ANTES de recortar: la ventana de datos
    // se calcula sobre el tramo que va a quedar a la vista, no sobre el anterior.
    if (state.replay && state.zoom.x) { state.zoom.x = followX(state.zoom.x, now_()); }
    var range = bounds();
    var cut = slice(range);

    Plotly.react("chart", priceTraces(cut).concat(boxTraces(cut), signalTraces(cut)), layout(range), {
      responsive: true, scrollZoom: true, displaylogo: false,
      // Sin las herramientas de dibujo de Plotly: lo que se marca a mano son la
      // caja, los recuadros y las líneas de este explorador, que se numeran, se
      // cuentan y se declaran en el estado. Las de Plotly no las ve nadie.
      modeBarButtonsToRemove: [
        "select2d", "lasso2d", "drawline", "drawopenpath", "drawclosedpath",
        "drawcircle", "drawrect", "eraseshape"
      ]
    });
    bindZoom();
    syncControls(range);
    document.getElementById("notes").textContent = notes(range, cut);
  }

  /* Qué se está viendo: par, temporalidad, tramo y cuántas velas. Y todo lo que
   * hay dibujado encima que no ha calculado nadie. Un gráfico que se calla lo
   * que se está mirando es un gráfico que se puede leer mal. */
  function notes(range, cut) {
    var b = bars();
    var visible = cut.end - cut.start;
    // Las marcas a mano se declaran siempre, también con la venda puesta: ahí
    // marcar dónde ves algo antes de revelar es justo el gesto de la prueba.
    var simulada = simCaption();
    var cuenta = accountCaption();
    var recuadros = rectCaption();
    var lineas = lineCaption();

    if (blindfolded()) {
      return "AUDITORÍA CIEGA · semilla " + state.seed + " · " + sym().label + " · " +
        label(state.chart) + " · " + range.from + " → " + range.to + " · " +
        visible.toLocaleString("es-ES") + " velas. Marca lo que veas y pulsa Revelar. " +
        boxCaption(cut) + ". " + signalCaption(cut) + ". " +
        "Sorteada dentro de " + state.scope.from + " → " + state.scope.to + "." +
        (simulada ? " · " + simulada : "") +
        (recuadros ? " · " + recuadros : "") +
        (lineas ? " · " + lineas : "") +
        (cuenta ? " · " + cuenta : "");
    }

    var text = sym().label + " · " + label(state.chart) + " · " + range.from + " → " +
      range.to + " · " + visible.toLocaleString("es-ES") + " velas en la ventana";
    if (state.replay) {
      var half = formingCandle();
      text = "REPLAY · " + sym().label + " · " + label(state.chart) + " · reloj " +
        iso(state.at).slice(0, 16) + " UTC · última vela cerrada " +
        stamp(b.t[state.cursor]) + " (nº " + (state.cursor + 1).toLocaleString("es-ES") +
        " de " + b.t.length.toLocaleString("es-ES") + ")" +
        (half
          ? (half.done
            ? " · vela en formación con " + half.done + " de " + half.total + " velas de " +
              label(half.timeframe)
            // Puede haber vela a medio armar sin que haya cerrado ninguna vela
            // del paso: es lo que pasa al llegar al diario desde H1.
            : " · vela en formación, todavía sin ninguna vela de " +
              label(half.timeframe) + " cerrada")
          : "") +
        " · sólo se dibuja hasta donde llega el reloj";
    }
    if (state.zoom.x || state.zoom.y) {
      text += " · encuadre manual: el zoom se mantiene entre pasos (Ajustar para soltarlo)";
    }
    // Lo que este explorador NO dibuja. Con el gráfico pelado, la ausencia de
    // marcas se lee como que ahí no pasó nada, y lo que pasa es que todavía no
    // hay quien lo calcule: eso hay que decirlo, no dejarlo suponer.
    text += " · SIN ESTRATEGIA: no hay entradas. " + boxCaption(cut) + ". " +
      signalCaption(cut) + ". Todo lo demás que se dibuja encima del precio lo pone tu mano";
    if (sym().skipped && sym().skipped.length) {
      text += " · temporalidades sin velas en " + sym().label + ": " +
        sym().skipped.join(" · ");
    }
    if (DATA.unavailable && DATA.unavailable.length) {
      text += " · pares no embebidos: " + DATA.unavailable.join(" · ");
    }
    if (!visible) {
      // Un gráfico vacío no puede quedarse callado: o el mercado estaba cerrado,
      // o las velas de esta temporalidad no llegan hasta aquí, y son dos cosas
      // muy distintas.
      text += ". No hay velas de " + label(state.chart) + " en este tramo: " +
        (b.t.length
          ? "el mercado estaba cerrado, o las embebidas empiezan en " + stamp(b.t[0]) +
            (b.truncated ? " porque el resto lo recortó max_explorer_bars" : "")
          : "el histórico no llega") + ".";
    }
    if (b.truncated) {
      text += ". Aviso: de las " + b.total.toLocaleString("es-ES") + " velas de " +
        label(state.chart) + " de " + sym().label + " sólo se han embebido las últimas " +
        b.t.length.toLocaleString("es-ES") + " (max_explorer_bars).";
    }
    if (state.blind && state.revealed) {
      text += " · revelado de la ventana ciega con semilla " + state.seed;
    }
    if (simulada) { text += " · " + simulada; }
    if (recuadros) { text += " · " + recuadros; }
    if (lineas) { text += " · " + lineas; }
    if (cuenta) { text += " · " + cuenta; }
    return text;
  }

  // --- Auditoría ciega ----------------------------------------------------------

  /* Anchura de la ventana que se sortea: la del preset elegido. Con "Todo" se
   * usa un mes, que es lo que se puede auditar de una sentada. */
  function blindWidth() {
    var preset = PRESETS.filter(function (p) { return p.id === state.preset; })[0];
    return preset && preset.days ? preset.days : 30;
  }

  function startBlind(seed) {
    // La ciega y el replay son dos pruebas distintas sobre la misma ventana: al
    // empezar una se sale de la otra en vez de dejar controles muertos.
    resetReplay();
    dropZoom();
    var range = bounds();
    if (!state.blind) { state.scope = { from: range.from, to: range.to }; }
    state.seed = seed;
    state.blind = true;
    state.revealed = false;

    var width = Math.min(blindWidth(), spanDays(state.scope.from, state.scope.to));
    var room = Math.max(0, spanDays(state.scope.from, state.scope.to) - width);
    var offset = Math.floor(rng(state.seed)() * (room + 1));
    state.from = shiftDays(state.scope.from, offset);
    state.to = shiftDays(state.from, width - 1);
    if (state.to > state.scope.to) { state.to = state.scope.to; }
    draw();
  }

  function resetBlind() {
    if (!state.blind) { return; }
    dropZoom();
    state.from = state.scope.from;
    state.to = state.scope.to;
    state.blind = false;
    state.revealed = false;
    state.scope = null;
  }

  function exitBlind() {
    if (!state.blind) { return; }
    resetBlind();
    draw();
  }

  function seedInput() { return document.getElementById("blind-seed"); }

  /* Si el propietario escribe una semilla, manda la suya y se reabre la misma
   * ventana. Si no, se sortea una nueva cada vez y se enseña: el campo muestra
   * siempre la semilla de la ventana que se está viendo, así que sin esta
   * distinción "Otra ventana" repetiría la anterior para siempre. */
  function chosenSeed() {
    var typed = parseInt(seedInput().value, 10);
    if (state.seedTyped && !isNaN(typed)) {
      state.seedTyped = false;
      return typed;
    }
    return Math.floor(Math.random() * 1000000);
  }

  // --- Replay -------------------------------------------------------------------

  /* Arranca en la fecha elegida con el cursor en la última vela ANTERIOR a ese
   * día: el primer paso descubre la primera vela de la fecha, y no la enseña ya
   * hecha. */
  function startReplay(day) {
    var t = bars().t;
    var index = lowerBound(t, dayStart(day)) - 1;
    if (index < 0) { index = 0; }
    if (index > t.length - 1) { index = t.length - 1; }
    if (!state.replay) {
      state.resume = { from: state.from, to: state.to, preset: state.preset };
    }
    resetBlind();
    pauseReplay();
    dropZoom();
    state.replay = true;
    state.cursor = index;
    state.sub = 0;
    state.at = now_();
    draw();
  }

  function resetReplay() {
    if (!state.replay) { return; }
    pauseReplay();
    dropZoom();
    state.replay = false;
    state.from = state.resume.from;
    state.to = state.resume.to;
    state.preset = state.resume.preset;
    state.resume = null;
    state.sub = 0;
  }

  function exitReplay() {
    if (!state.replay) { return; }
    resetReplay();
    draw();
  }

  /* Al cambiar de temporalidad —o de PAR— en mitad del replay el reloj no se
   * mueve: se busca la última vela de la nueva serie que ya hubiera cerrado a
   * esa misma hora. Si no se hiciera, el índice del cursor —que es de otro
   * array— señalaría a una fecha cualquiera.
   *
   * Y lo que va corrido de la vela en curso se conserva igual: si en H4 llevas
   * dos velas dentro del día, el diario tiene que enseñar su vela a medio armar
   * con esas dos horas dentro. El reloj no puede ir a más resolución que la
   * temporalidad inferior de la nueva: lo que no completa una de sus velas se
   * queda fuera. */
  function alignCursor(at) {
    var t = bars().t;
    var index = lowerBound(t, at - span(state.chart) + 1) - 1;
    state.cursor = Math.min(Math.max(index, 0), t.length - 1);
    state.sub = 0;
    if (!state.forming) { return; }
    var edges = formingRange();
    if (!edges) { return; }
    var fine = barsOf(edges.timeframe).t;
    // Velas inferiores cerradas a esa hora que caen dentro de la que se forma.
    var formed = lowerBound(fine, at - span(edges.timeframe) + 1) - edges.from;
    state.sub = Math.max(0, Math.min(formed, edges.to - edges.from - 1));
  }

  /* Un paso: o se forma un trozo más de la vela en curso, o la vela cierra.
   * Nunca las dos cosas a la vez. Devuelve si se movió algo. */
  function stepReplay(direction) {
    var t = bars().t;
    if (direction > 0) {
      if (state.sub < subSteps()) { state.sub += 1; }
      else if (state.cursor + 1 < t.length) { state.cursor += 1; state.sub = 0; }
      else { return false; }
    } else if (state.sub > 0) {
      state.sub -= 1;
    } else if (state.cursor > 0) {
      state.cursor -= 1;
      state.sub = 0;
    } else {
      return false;
    }
    state.at = clock();
    draw();
    return true;
  }

  function playReplay() {
    if (!state.replay || state.playing) { return; }
    state.playing = true;
    schedule();
    draw();
  }

  /* Encadenada con `setTimeout` y no con `setInterval`: si un paso tarda más que
   * el intervalo los pasos no se apilan. */
  function schedule() {
    timer = setTimeout(function () {
      timer = null;
      if (!state.playing) { return; }
      if (!stepReplay(1)) { pauseReplay(); draw(); }
      else { schedule(); }
    }, state.speed);
  }

  function pauseReplay() {
    if (timer !== null) { clearTimeout(timer); timer = null; }
    state.playing = false;
  }

  // --- Controles ----------------------------------------------------------------

  /* Atajos de temporalidad: una tecla por gráfico, declaradas en el payload. Si
   * el par no trae ese gráfico —un histórico H1 no da para M15— la tecla no hace
   * nada. */
  var CHART_KEYS = {};
  Object.keys(DATA.keys).forEach(function (timeframe) {
    CHART_KEYS[DATA.keys[timeframe]] = timeframe;
  });

  function chartShortcut(timeframe) { return DATA.keys[timeframe] || "sin atajo"; }

  /* Cambia el gráfico activo. Conserva el reloj canónico del replay, no el que
   * se lee en este gráfico: si vienes de pasar por el diario, lo que allí no
   * cabía sigue estando aquí. */
  function selectChart(chart) {
    if (charts().indexOf(chart) < 0) { return; }
    var at = state.replay ? state.at : null;
    state.chart = chart;
    if (at !== null) { alignCursor(at); }
    draw();
  }

  /* --- El par ------------------------------------------------------------------
   *
   * Cambiar de par es cambiar de qué array se lee, y arrastra tres cosas:
   *
   *   · las MARCAS A MANO se guardan y se reponen. Un recuadro puesto en el oro
   *     a 2.400 no dice nada sobre el euro, y dejarlo ahí lo sacaría de la
   *     pantalla o —peor— lo dejaría flotando encima de un precio cualquiera;
   *   · el ENCUADRE MANUAL se suelta: el rango de precios de un par no encuadra
   *     el de otro, y el zoom en horizontal se puede volver a hacer;
   *   · el GRÁFICO se ajusta si el par nuevo no trae esa temporalidad, y el
   *     RELOJ del replay se conserva realineando el cursor sobre las velas
   *     nuevas, que es lo mismo que se hace al saltar de temporalidad.
   *
   * La ventana de fechas y la cuenta simulada NO se tocan: mirar el mismo tramo
   * en dos pares es lo que se quiere poder hacer, y la cuenta es una sola. */
  function stashMarks() {
    state.stashed[sym().id] = {
      sim: state.sim, rects: state.rects, lines: state.lines
    };
  }

  function restoreMarks() {
    var saved = state.stashed[sym().id];
    state.sim = saved ? saved.sim : null;
    state.rects = saved ? saved.rects : [];
    state.lines = saved ? saved.lines : [];
  }

  function selectSymbol(index) {
    if (index < 0 || index >= DATA.symbols.length || index === state.symbol) { return; }
    var at = state.replay ? state.at : null;
    stashMarks();
    state.arming = null;
    state.symbol = index;
    restoreMarks();
    if (charts().indexOf(state.chart) < 0) { state.chart = charts()[0]; }
    dropZoom();
    if (at !== null) { alignCursor(at); }
    // Los botones de temporalidad son los del par: uno puede no llegar a M15.
    buildChartButtons();
    draw();
  }

  function buildSymbolButtons() {
    var container = document.getElementById("symbol-buttons");
    if (!container) { return; }
    DATA.symbols.forEach(function (item, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = item.label;
      button.dataset.symbol = item.id;
      button.title = item.label + " · lado " + item.side + " · " +
        item.charts.map(label).join(", ") + " · precio con " + item.decimals +
        " decimales (el pip es la última)\nCambiar de par guarda lo que hayas " +
        "marcado a mano en éste y repone lo del otro.";
      button.addEventListener("click", function () { selectSymbol(index); });
      container.appendChild(button);
    });
  }

  function buildChartButtons() {
    var container = document.getElementById("tf-buttons");
    container.innerHTML = "";
    charts().forEach(function (chart) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = label(chart);
      button.dataset.tf = chart;
      button.title = "Velas de " + label(chart) + " de " + sym().label +
        ". Atajo de teclado: " + chartShortcut(chart);
      button.addEventListener("click", function () { selectChart(chart); });
      container.appendChild(button);
    });
  }

  function buildPresetButtons() {
    var container = document.getElementById("preset-buttons");
    PRESETS.forEach(function (preset) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = preset.label;
      button.dataset.preset = preset.id;
      button.addEventListener("click", function () {
        state.preset = preset.id;
        state.from = state.to = null;
        dropZoom();
        draw();
      });
      container.appendChild(button);
    });
  }

  /* Avanza o retrocede la ventana actual una anchura completa. Los tramos van
   * pegados y sin solapar: el "hasta" de uno es el día anterior al "desde" del
   * siguiente, así ninguna vela se mira dos veces. */
  function step(direction) {
    var range = bounds();
    var width = spanDays(range.from, range.to) + 1;
    var from = shiftDays(range.from, direction * width);
    var to = shiftDays(range.to, direction * width);
    if (from < range.first) { from = range.first; to = shiftDays(from, width - 1); }
    if (to > range.last) { to = range.last; from = shiftDays(to, -(width - 1)); }
    state.from = from < range.first ? range.first : from;
    state.to = to > range.last ? range.last : to;
    dropZoom();
    draw();
  }

  function syncControls(range) {
    var usingPreset = !state.from && !state.to;
    document.querySelectorAll("#symbol-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.symbol === sym().id));
    });
    document.querySelectorAll("#tf-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.tf === state.chart));
    });
    document.querySelectorAll("#view-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
    });
    document.querySelectorAll("#preset-buttons button").forEach(function (button) {
      button.setAttribute(
        "aria-pressed", String(usingPreset && button.dataset.preset === state.preset)
      );
      // Durante el replay la ventana la manda el cursor: los controles de
      // periodo se apagan en vez de mentir sobre lo que se está viendo.
      button.disabled = state.replay;
    });
    var from = document.getElementById("from");
    var to = document.getElementById("to");
    from.min = to.min = range.first;
    from.max = to.max = range.last;
    from.value = range.from;
    to.value = range.to;

    // Sólo hay algo que soltar si el encuadre está tomado a mano.
    document.getElementById("zoom-reset").disabled = !state.zoom.x && !state.zoom.y;

    document.querySelectorAll("#sim-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.side === state.arming));
    });
    document.getElementById("sim-clear").disabled = !state.sim && !simArming();
    syncRatioReadout();
    // Armado, el gráfico deja de ser sólo para mirar: el cursor lo dice.
    var canvas = document.getElementById("chart");
    if (canvas && canvas.style) { canvas.style.cursor = state.arming ? "crosshair" : ""; }

    // Los recuadros a mano: cada botón dice si está esperando el clic y se pinta
    // del color de su recuadro, y sin recuadros puestos no hay nada que quitar.
    document.querySelectorAll("#rect-buttons button").forEach(function (button) {
      var kind = button.dataset.kind;
      var pressed = armedRect() === kind;
      button.setAttribute("aria-pressed", String(pressed));
      button.style.color = pressed ? COLORS.surface : rectColor(kind);
      button.style.background = pressed ? rectColor(kind) : "";
      button.style.borderColor = pressed ? rectColor(kind) : "";
    });
    document.getElementById("rect-undo").disabled = !state.rects.length;
    document.getElementById("rect-clear").disabled = !state.rects.length;

    // Las líneas a mano: lo mismo, cada botón con el color de su línea.
    document.querySelectorAll("#line-buttons button").forEach(function (button) {
      var kind = button.dataset.kind;
      var pressed = armedLine() === kind;
      button.setAttribute("aria-pressed", String(pressed));
      button.style.color = pressed ? COLORS.surface : lineColor(kind);
      button.style.background = pressed ? lineColor(kind) : "";
      button.style.borderColor = pressed ? lineColor(kind) : "";
    });
    document.getElementById("line-undo").disabled = !state.lines.length;
    document.getElementById("line-clear").disabled = !state.lines.length;

    syncAccount();
    seedInput().value = state.seed === null ? "" : String(state.seed);
    document.getElementById("blind-reveal").disabled = !blindfolded();
    document.getElementById("blind-exit").disabled = !state.blind;
    document.getElementById("blind-start").textContent =
      state.blind ? "Otra ventana" : "Empezar";

    syncReplay(range);
  }

  function syncReplay(range) {
    var group = document.getElementById("replay-group");
    if (group) { group.className = state.replay ? "group on" : "group"; }

    var day = document.getElementById("replay-date");
    day.min = range.first;
    day.max = range.last;
    if (state.replay) { day.value = dayOf(bars().t[state.cursor]); }
    else if (!day.value) { day.value = range.to; }

    ["replay-step", "replay-back", "replay-play", "replay-exit"].forEach(function (id) {
      document.getElementById(id).disabled = !state.replay;
    });
    document.getElementById("replay-start").textContent =
      state.replay ? "Reiniciar" : "Empezar";
    document.getElementById("replay-play").textContent = state.playing ? "⏸" : "▶";
    document.getElementById("replay-forming").checked = state.forming;
    document.getElementById("replay-window").value = String(state.window);
    ["from", "to", "prev", "next"].forEach(function (id) {
      document.getElementById(id).disabled = state.replay;
    });
  }

  function bindControls() {
    document.querySelectorAll("#view-buttons button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.view = button.dataset.view;
        draw();
      });
    });
    document.getElementById("zoom-reset").addEventListener("click", releaseZoom);
    document.getElementById("prev").addEventListener("click", function () { step(-1); });
    document.getElementById("next").addEventListener("click", function () { step(1); });
    ["from", "to"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", function (event) {
        var value = event.target.value;
        if (!value) { return; }
        state[id] = value;
        var range = bounds();
        if (range.from > range.to) { state[id === "from" ? "to" : "from"] = value; }
        dropZoom();
        draw();
      });
    });
    seedInput().addEventListener("change", function () { state.seedTyped = true; });
    document.getElementById("blind-start").addEventListener("click", function () {
      startBlind(chosenSeed());
    });
    document.getElementById("blind-reveal").addEventListener("click", function () {
      if (!state.blind) { return; }
      state.revealed = true;
      draw();
    });
    document.getElementById("blind-exit").addEventListener("click", exitBlind);
    document.getElementById("sim-clear").addEventListener("click", clearSim);
    bindReplay();
    bindArrowKeys();
  }

  function bindReplay() {
    document.getElementById("replay-start").addEventListener("click", function () {
      var day = document.getElementById("replay-date").value;
      startReplay(day || bounds().last);
    });
    document.getElementById("replay-step").addEventListener("click", function () {
      if (state.replay) { stepReplay(1); }
    });
    document.getElementById("replay-back").addEventListener("click", function () {
      if (state.replay) { stepReplay(-1); }
    });
    document.getElementById("replay-play").addEventListener("click", toggleReplay);
    document.getElementById("replay-exit").addEventListener("click", exitReplay);
    document.getElementById("replay-forming").addEventListener("change", function (event) {
      state.forming = event.target.checked;
      // Sin vela en formación el reloj vuelve al último cierre: es lo que se está
      // enseñando, y el reloj no puede prometer más de lo que se ve.
      if (!state.forming) { state.sub = 0; state.at = now_(); }
      draw();
    });
    document.getElementById("replay-speed").addEventListener("change", function (event) {
      var speed = parseInt(event.target.value, 10);
      if (!isNaN(speed) && speed > 0) { state.speed = speed; }
    });
    document.getElementById("replay-window").addEventListener("change", function (event) {
      var count = parseInt(event.target.value, 10);
      if (isNaN(count) || count < 2) { return; }
      state.window = count;
      dropZoom();
      draw();
    });
  }

  function toggleReplay() {
    if (!state.replay) { return; }
    if (state.playing) { pauseReplay(); draw(); } else { playReplay(); }
  }

  /* ◀ ▶ también con las flechas del teclado, la barra espaciadora para arrancar
   * y parar el replay, una tecla por temporalidad (d/4/1/m) y las flechas
   * ARRIBA y ABAJO para cambiar de par. Se ignoran mientras el foco está en un
   * campo de texto: ahí las teclas escriben y robarlas haría imposible poner una
   * fecha o una semilla.
   *
   * En replay las flechas izquierda y derecha dan pasos en vez de mover la
   * ventana: es el mismo gesto —avanzar y retroceder en el tiempo— a la escala
   * de lo que se mira. */
  function bindArrowKeys() {
    if (!document.addEventListener) { return; }
    document.addEventListener("keydown", function (event) {
      var arrow = event.key === "ArrowLeft" || event.key === "ArrowRight";
      var vertical = event.key === "ArrowUp" || event.key === "ArrowDown";
      var space = event.key === " " || event.key === "Spacebar";
      // Escape desarma lo que esté esperando un clic: un botón armado tiene que
      // poder soltarse sin plantar nada.
      var escape = event.key === "Escape" || event.key === "Esc";
      // Con Ctrl/Alt/Meta la tecla es del navegador (Ctrl+D marca la página):
      // ahí no hay atajo de temporalidad.
      var modified = event.ctrlKey || event.altKey || event.metaKey;
      var chart = modified ? null : CHART_KEYS[String(event.key).toLowerCase()];
      if (!arrow && !vertical && !space && !chart && !escape) { return; }
      var focused = document.activeElement;
      var tag = focused && focused.tagName ? focused.tagName.toUpperCase() : "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { return; }
      // La barra espaciadora sobre un botón lo pulsa: ahí no se roba.
      if (space && (tag === "BUTTON" || !state.replay)) { return; }
      if (escape) {
        if (state.arming) { state.arming = null; draw(); }
        return;
      }
      if (event.preventDefault) { event.preventDefault(); }
      if (chart) { selectChart(chart); return; }
      if (space) { toggleReplay(); return; }
      if (vertical) {
        selectSymbol(state.symbol + (event.key === "ArrowUp" ? -1 : 1));
        return;
      }
      var back = event.key === "ArrowLeft" ? -1 : 1;
      if (state.replay) { stepReplay(back); } else { step(back); }
    });
  }

  buildSymbolButtons();
  buildChartButtons();
  buildPresetButtons();
  buildSimButtons();
  buildRectButtons();
  buildLineButtons();
  buildAccountButtons();
  buildRiskModes();
  bindControls();
  bindAxisScaling();
  bindSim();
  bindRects();
  bindLines();
  bindAccount();
  draw();
})();
