/* Arranca explorer.js contra un DOM mínimo y simula los controles.
 *
 * No sustituye a mirar el explorador en un navegador, pero sí detecta lo que
 * más suele romperse: identificadores que no existen, campos mal nombrados en
 * el payload y excepciones dentro del ciclo de render. Además comprueba que los
 * controles —el selector de PAR, velas/líneas, temporalidades, navegación por
 * fechas, replay y las marcas a mano— cambian la figura de verdad.
 *
 * Uso: node explorer_dom_stub.js <explorer.js> <payload.json>
 */
const fs = require('fs');

const [, , scriptPath, payloadPath] = process.argv;

const plotCalls = [];
const missing = [];
const elements = {};

function makeElement(id) {
  return {
    id: id,
    dataset: {},
    checked: true,
    value: '',
    min: '',
    max: '',
    title: '',
    className: '',
    textContent: '',
    children: [],
    attributes: {},
    listeners: {},
    style: {},
    set innerHTML(value) { if (value === '') { this.children = []; } },
    get innerHTML() { return ''; },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    // Los eventos de Plotly se enganchan al div con `on`, no con addEventListener.
    on(type, handler) { this.addEventListener(type, handler); },
    // El gesto de escalar sobre los ejes necesita saber dónde está el gráfico.
    getBoundingClientRect() { return { left: 0, top: 0, width: 1200, height: 720 }; },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name] ?? null; },
    appendChild(child) { this.children.push(child); return child; },
    fire(type, event) {
      (this.listeners[type] || []).forEach(function (handler) { handler(event || {}); });
    },
  };
}

function declare(id) {
  elements[id] = makeElement(id);
  return elements[id];
}

// Elementos que la plantilla HTML declara de verdad.
['symbol-group', 'symbol-buttons',
 'tf-buttons', 'view-buttons', 'preset-buttons', 'chart', 'zoom-reset',
 'prev', 'next', 'from', 'to',
 'blind-seed', 'blind-start', 'blind-reveal', 'blind-exit',
 'sim-group', 'sim-buttons', 'sim-rr', 'sim-clear',
 'rect-group', 'rect-buttons', 'rect-undo', 'rect-clear',
 'line-group', 'line-buttons', 'line-undo', 'line-clear',
 'account-group', 'account-initial', 'account-mode', 'account-risk',
 'account-buttons', 'account-undo', 'account-reset', 'account-copy', 'account-summary',
 'replay-group', 'replay-date', 'replay-start', 'replay-back', 'replay-step',
 'replay-play', 'replay-exit', 'replay-forming', 'replay-speed', 'replay-window',
 'notes', 'explorer-data'].forEach(declare);

elements['explorer-data'].textContent = fs.readFileSync(payloadPath, 'utf8');

const viewButtons = ['candles', 'line'].map(function (view) {
  const button = makeElement('view-' + view);
  button.dataset.view = view;
  return button;
});

const documentListeners = {};

// El portapapeles. `writeText` devuelve algo con `catch`, como la promesa de
// verdad, para que el explorador pueda encadenarlo.
const copiado = [];
// `navigator` es un global de node y no se deja reasignar: hay que redefinirlo.
Object.defineProperty(global, 'navigator', {
  configurable: true,
  writable: true,
  value: {
    clipboard: {
      writeText(text) { copiado.push(text); return { catch() { return null; } }; },
    },
  },
});

global.document = {
  activeElement: null,
  getElementById(id) {
    if (!elements[id]) { missing.push(id); declare(id); }
    return elements[id];
  },
  createElement() { return makeElement('created'); },
  createTextNode(text) { return { text: text }; },
  addEventListener(type, handler) {
    (documentListeners[type] = documentListeners[type] || []).push(handler);
  },
  querySelectorAll(selector) {
    if (selector === '#symbol-buttons button') { return elements['symbol-buttons'].children; }
    if (selector === '#tf-buttons button') { return elements['tf-buttons'].children; }
    if (selector === '#preset-buttons button') { return elements['preset-buttons'].children; }
    if (selector === '#sim-buttons button') { return elements['sim-buttons'].children; }
    if (selector === '#rect-buttons button') { return elements['rect-buttons'].children; }
    if (selector === '#line-buttons button') { return elements['line-buttons'].children; }
    if (selector === '#account-buttons button') { return elements['account-buttons'].children; }
    if (selector === '#view-buttons button') { return viewButtons; }
    missing.push(selector);
    return [];
  },
};

function fireDocument(type, event) {
  (documentListeners[type] || []).forEach(function (handler) { handler(event); });
}

function pressKey(key, focusedTag) {
  global.document.activeElement = focusedTag ? { tagName: focusedTag } : null;
  (documentListeners['keydown'] || []).forEach(function (handler) {
    handler({ key: key, preventDefault() {} });
  });
  global.document.activeElement = null;
}

/* Lo que NO ha puesto una mano. Las velas y la vela en formación son precio;
 * todo lo demás es calculado y se apunta con su nombre en `calculated`. Hoy las
 * capas calculadas son la caja de las 02:00 (H3 y H1) y la señal de H1:
 * cualquier otra, el test la señala. */
function priceLayer(name) {
  return /^(Velas |Cierres |Vela en formación)/.test(name || '');
}

function boxLayer(name) {
  return /^Caja de las 02:00 · (rango bajista|rango alcista|vela)$/.test(name || '');
}

function signalLayer(name) {
  return /^Señal H1 · (línea del turtle soup|otro extremo|confirmación)$/.test(name || '');
}

function simShape(shape) {
  return String(shape.name || '').indexOf('sim-') === 0;
}

function rectShape(shape) {
  return String(shape.name || '').indexOf('rect-') === 0;
}

function lineShape(shape) {
  return String(shape.name || '').indexOf('line-') === 0;
}

function handDrawn(shape) {
  return simShape(shape) || rectShape(shape) || lineShape(shape);
}

/* El punto más a la derecha de todo lo que NO ha dibujado una mano. En el
 * replay nada de eso puede caer más allá del reloj. */
function furthest(traces, layout) {
  const points = [];
  traces.forEach(function (trace) {
    (trace.x || []).forEach(function (value) { if (value) { points.push(value); } });
  });
  (layout.shapes || []).forEach(function (shape) {
    if (!handDrawn(shape)) { points.push(shape.x1); }
  });
  return points.length ? points.sort()[points.length - 1] : null;
}

const relayoutCalls = [];

global.Plotly = {
  // Lo que el gesto de escalar sobre los ejes le pide a Plotly. Se reemite como
  // `plotly_relayout`, que es lo que hace Plotly de verdad: así el recorrido
  // comprueba también que el encuadre queda guardado.
  relayout(target, update) {
    relayoutCalls.push(update);
    elements['chart'].fire('plotly_relayout', update);
  },
  react(target, traces, layout) {
    plotCalls.push({
      maxX: furthest(traces, layout),
      target: target,
      // Todo lo que no es precio: lo ha calculado alguien.
      calculated: traces.filter(function (trace) { return !priceLayer(trace.name); })
        .map(function (trace) { return trace.name; }),
      // Las cajas: cuántos rectángulos (cinco vértices y un corte cada uno), sus
      // vértices y el punto más a la derecha, que en replay no puede pasar del reloj.
      boxes: traces.filter(function (trace) { return boxLayer(trace.name); })
        .map(function (trace) {
          const xs = (trace.x || []).filter(function (value) { return value; });
          return {
            name: trace.name,
            count: (trace.x || []).filter(function (value) { return value === null; }).length,
            x: trace.x.slice(),
            y: trace.y.slice(),
            maxX: xs.length ? xs.slice().sort()[xs.length - 1] : null,
            fill: trace.fill || null,
            color: (trace.line && trace.line.color) || null,
            captions: (trace.text || []).filter(function (value) { return value; }),
          };
        }),
      // La señal de H1: los segmentos de cada línea (dos puntos y un corte cada
      // uno) y los triángulos.
      signals: traces.filter(function (trace) { return signalLayer(trace.name); })
        .map(function (trace) {
          const xs = (trace.x || []).filter(function (value) { return value; });
          return {
            name: trace.name,
            x: trace.x.slice(),
            y: trace.y.slice(),
            maxX: xs.length ? xs.slice().sort()[xs.length - 1] : null,
            color: (trace.line && trace.line.color) || (trace.marker && trace.marker.color) || null,
            dash: (trace.line && trace.line.dash) || null,
            symbols: (trace.marker && trace.marker.symbol) || null,
            captions: (trace.text || []).filter(function (value) { return value; }),
          };
        }),
      traces: traces.map(function (trace) {
        return {
          name: trace.name,
          type: trace.type,
          points: (trace.x && trace.x.length) || 0,
          color: (trace.line && trace.line.color) || null,
          captions: trace.text && trace.text.length <= 200 ? trace.text.slice() : null,
        };
      }),
      bars: (traces[0] && traces[0].x && traces[0].x.length) || 0,
      firstBar: traces[0] && traces[0].x && traces[0].x[0],
      lastBar: traces[0] && traces[0].x && traces[0].x[traces[0].x.length - 1],
      hover: traces[0] && traces[0].text && traces[0].text[0],
      // Todo lo que hay en `shapes` lo ha puesto una mano: si algún día aparece
      // ahí algo que no lo sea, este recuento deja de ser cero.
      shapes: (layout.shapes || []).filter(function (shape) {
        return !handDrawn(shape);
      }).length,
      sim: (layout.shapes || []).filter(simShape).map(function (shape) {
        return {
          name: shape.name, type: shape.type,
          x0: shape.x0, x1: shape.x1, y0: shape.y0, y1: shape.y1,
          label: (shape.label && shape.label.text) || null,
        };
      }),
      rect: (layout.shapes || []).filter(rectShape).map(function (shape) {
        return {
          name: shape.name, type: shape.type,
          x0: shape.x0, x1: shape.x1, y0: shape.y0, y1: shape.y1,
          color: (shape.line && shape.line.color) || null,
          dash: (shape.line && shape.line.dash) || null,
          fillcolor: shape.fillcolor || null,
          label: (shape.label && shape.label.text) || null,
        };
      }),
      line: (layout.shapes || []).filter(lineShape).map(function (shape) {
        return {
          name: shape.name, type: shape.type,
          x0: shape.x0, x1: shape.x1, y0: shape.y0, y1: shape.y1,
          color: (shape.line && shape.line.color) || null,
          dash: (shape.line && shape.line.dash) || null,
          width: (shape.line && shape.line.width) || null,
          label: (shape.label && shape.label.text) || null,
        };
      }),
      yTickFormat: layout.yaxis && layout.yaxis.tickformat,
      xRange: (layout.xaxis && layout.xaxis.range) || null,
      yRange: (layout.yaxis && layout.yaxis.range) || null,
    });
  },
};

function pressed(container, key) {
  const found = elements[container].children.filter(function (button) {
    return button.getAttribute('aria-pressed') === 'true';
  })[0];
  return found ? (found.dataset[key] ?? null) : null;
}

function snapshot(label) {
  return {
    label: label,
    symbol: pressed('symbol-buttons', 'symbol'),
    chart: pressed('tf-buttons', 'tf'),
    chartTabs: elements['tf-buttons'].children.map(function (b) { return b.dataset.tf; }),
    plot: plotCalls[plotCalls.length - 1],
    notes: elements['notes'].textContent,
    from: elements['from'].value,
    to: elements['to'].value,
    replayDate: elements['replay-date'].value,
    zoomFree: elements['zoom-reset'].disabled === true,
    replayPlay: elements['replay-play'].textContent,
    lastRelayout: relayoutCalls[relayoutCalls.length - 1] || null,
    replayLocked: elements['from'].disabled === true && elements['next'].disabled === true,
    simArmed: pressed('sim-buttons', 'side'),
    simClearDisabled: elements['sim-clear'].disabled === true,
    // El R:R que el panel dice que hay dibujado: siempre medido de la caja.
    simReadout: elements['sim-rr'].textContent,
    simReadoutSource: elements['sim-rr'].dataset.source || '',
    simReadoutTitle: elements['sim-rr'].title,
    simCursor: elements['chart'].style.cursor || '',
    rectArmed: pressed('rect-buttons', 'kind'),
    rectUndoDisabled: elements['rect-undo'].disabled === true,
    rectClearDisabled: elements['rect-clear'].disabled === true,
    lineArmed: pressed('line-buttons', 'kind'),
    lineUndoDisabled: elements['line-undo'].disabled === true,
    lineClearDisabled: elements['line-clear'].disabled === true,
    account: {
      summary: elements['account-summary'].textContent,
      initial: elements['account-initial'].value,
      mode: elements['account-mode'].value,
      risk: elements['account-risk'].value,
      resultsDisabled: elements['account-buttons'].children.map(function (button) {
        return button.disabled === true;
      }),
      undoDisabled: elements['account-undo'].disabled === true,
      resetDisabled: elements['account-reset'].disabled === true,
      copyDisabled: elements['account-copy'].disabled === true,
    },
    copiado: copiado.length ? copiado[copiado.length - 1] : null,
  };
}

global.window = global;
eval(fs.readFileSync(scriptPath, 'utf8'));

const payload = JSON.parse(elements['explorer-data'].textContent);
const steps = [];
const symbols = elements['symbol-buttons'].children;
const tabs = function () { return elements['tf-buttons'].children; };
const presets = elements['preset-buttons'].children;

function minuteOf(text) {
  return Math.round(Date.parse(String(text).replace(' ', 'T') + 'Z') / 60000);
}

function zoomTo(x, y) {
  elements['chart'].fire('plotly_relayout', {
    'xaxis.range[0]': new Date(x[0] * 60000).toISOString().replace('T', ' ').slice(0, 19),
    'xaxis.range[1]': new Date(x[1] * 60000).toISOString().replace('T', ' ').slice(0, 19),
    'yaxis.range[0]': y[0],
    'yaxis.range[1]': y[1],
  });
}

function selectSymbol(id) {
  symbols.filter(function (button) { return button.dataset.symbol === id; })
    .forEach(function (button) { button.fire('click'); });
}

function selectChart(tf) {
  tabs().filter(function (button) { return button.dataset.tf === tf; })
    .forEach(function (button) { button.fire('click'); });
}

steps.push(snapshot('salida'));

// --- El par ------------------------------------------------------------------
//
// Cambiar de par cambia las velas, los decimales del eje y los botones de
// temporalidad, y NO cambia la ventana de fechas ni la cuenta.
payload.symbols.forEach(function (item) {
  selectSymbol(item.id);
  steps.push(snapshot('par-' + item.id));
});
// Las flechas arriba y abajo recorren los pares, salvo con el foco en un campo.
selectSymbol(payload.symbols[0].id);
pressKey('ArrowDown');
steps.push(snapshot('par-teclado-abajo'));
pressKey('ArrowUp');
steps.push(snapshot('par-teclado-arriba'));
pressKey('ArrowDown', 'INPUT');
steps.push(snapshot('par-teclado-en-un-campo'));

// --- Temporalidades, periodo y vista -----------------------------------------
selectSymbol(payload.symbols[0].id);
presets[presets.length - 1].fire('click');
tabs().forEach(function (tab) {
  tab.fire('click');
  steps.push(snapshot('grafico-' + tab.dataset.tf));
});

tabs()[0].fire('click');
presets[0].fire('click');
steps.push(snapshot('todo'));
// H1 con todo el histórico: todas las señales del tramo.
if (payload.symbols[0].charts.indexOf('H1') >= 0) {
  selectChart('H1');
  steps.push(snapshot('h1-todo'));
  tabs()[0].fire('click');
}
presets[presets.length - 1].fire('click');
steps.push(snapshot('preset-corto'));
elements['prev'].fire('click');
steps.push(snapshot('ventana-anterior'));
elements['next'].fire('click');
steps.push(snapshot('ventana-siguiente'));

// Las flechas del teclado mueven la ventana igual que los botones, salvo con el
// foco en un campo de texto.
pressKey('ArrowLeft');
steps.push(snapshot('teclado-izquierda'));
pressKey('ArrowRight');
steps.push(snapshot('teclado-derecha'));
pressKey('ArrowLeft', 'INPUT');
steps.push(snapshot('teclado-en-un-campo'));

// Atajos de temporalidad: una tecla por gráfico, salvo con el foco en un campo.
Object.keys(payload.keys).forEach(function (timeframe) {
  pressKey(payload.keys[timeframe]);
  steps.push(snapshot('teclado-tf-' + timeframe));
});
pressKey(payload.keys[payload.symbols[0].charts[0]], 'INPUT');
steps.push(snapshot('teclado-tf-en-un-campo'));

// Un gráfico que sólo trae un par: al pasar al que no lo tiene se cae al
// primero de los suyos, no a un gráfico vacío.
const propio = payload.symbols[0].charts.find(function (tf) {
  return payload.symbols[1] && payload.symbols[1].charts.indexOf(tf) < 0;
});
if (propio) {
  selectSymbol(payload.symbols[0].id);
  selectChart(propio);
  steps.push(snapshot('grafico-propio-' + propio));
  selectSymbol(payload.symbols[1].id);
  steps.push(snapshot('grafico-propio-otro-par'));
  selectSymbol(payload.symbols[0].id);
}

viewButtons[1].fire('click');
steps.push(snapshot('lineas'));
viewButtons[0].fire('click');
steps.push(snapshot('velas'));

// --- Replay -------------------------------------------------------------------
const h4 = payload.symbols[0].charts.indexOf('H4') >= 0 ? 'H4' : payload.symbols[0].charts[0];
selectChart(h4);
presets[0].fire('click');
const serie = payload.symbols[0].bars[h4].t;
const arranque = new Date(serie[Math.floor(serie.length / 2)] * 60000)
  .toISOString().slice(0, 10);
elements['replay-date'].value = arranque;
elements['replay-start'].fire('click');
steps.push(snapshot('replay-inicio'));
elements['replay-step'].fire('click');
steps.push(snapshot('replay-paso'));
elements['replay-back'].fire('click');
steps.push(snapshot('replay-atras'));
elements['replay-forming'].fire('change', { target: { checked: false } });
steps.push(snapshot('replay-sin-vela-en-formacion'));
elements['replay-forming'].fire('change', { target: { checked: true } });

// El reloj se conserva al saltar de temporalidad Y al cambiar de par.
const relojEnH4 = snapshot('replay-en-h4');
steps.push(relojEnH4);
selectChart(payload.symbols[0].charts[0]);
steps.push(snapshot('replay-en-el-mayor'));
if (payload.symbols.length > 1) {
  selectSymbol(payload.symbols[1].id);
  steps.push(snapshot('replay-otro-par'));
  selectSymbol(payload.symbols[0].id);
}
// En H3, la capa calculada: tampoco puede pasar del reloj.
if (payload.symbols[0].charts.indexOf('H3') >= 0) {
  selectChart('H3');
  steps.push(snapshot('replay-en-h3'));
}
// En H1, la caja y la señal: tampoco pueden pasar del reloj.
if (payload.symbols[0].charts.indexOf('H1') >= 0) {
  selectChart('H1');
  steps.push(snapshot('replay-en-h1'));
}
selectChart(h4);

// El encuadre hecho a mano sobrevive a los pasos: la ventana sólo se desplaza
// para seguir al presente.
const spanChart = payload.symbols[0].spans[h4];
elements['replay-window'].fire('change', { target: { value: '40' } });
const presente = minuteOf(plotCalls[plotCalls.length - 1].lastBar) + spanChart;
const precios = [
  payload.symbols[0].bars[h4].l[Math.floor(serie.length / 2)] * 0.98,
  payload.symbols[0].bars[h4].h[Math.floor(serie.length / 2)] * 1.02,
];
zoomTo([presente - 200 * spanChart, presente], precios);
elements['replay-step'].fire('click');
steps.push(snapshot('replay-con-zoom'));
elements['zoom-reset'].fire('click');
steps.push(snapshot('replay-zoom-suelto'));
elements['replay-exit'].fire('click');
steps.push(snapshot('replay-fuera'));

// --- Escalar arrastrando sobre los ejes ---------------------------------------
function arrastrarEje(desde, hasta) {
  elements['chart'].fire('mousedown', {
    clientX: desde[0], clientY: desde[1],
    preventDefault() {}, stopPropagation() {},
  });
  fireDocument('mousemove', {
    clientX: hasta[0], clientY: hasta[1], preventDefault() {},
  });
  fireDocument('mouseup', {});
}

zoomTo([presente - 100 * spanChart, presente], precios);
// Sobre la banda de los precios (x < 66): comprime y estira la vertical.
arrastrarEje([20, 300], [20, 450]);
steps.push(snapshot('eje-precios-arrastrado'));
// Sobre la banda de las fechas (y > 720 - 44): abre y cierra de lado.
arrastrarEje([600, 700], [400, 700]);
steps.push(snapshot('eje-fechas-arrastrado'));
elements['zoom-reset'].fire('click');

// --- Marcas a mano: la caja simulada ------------------------------------------
selectChart(payload.symbols[0].charts[0]);
presets[0].fire('click');
const spanMayor = payload.symbols[0].spans[payload.symbols[0].charts[0]];
const simX = [
  minuteOf(plotCalls[plotCalls.length - 1].lastBar) - 200 * spanMayor,
  minuteOf(plotCalls[plotCalls.length - 1].lastBar),
];
const simY = precios;
zoomTo(simX, simY);

// El mismo cálculo que hace el explorador: MARGIN sobre el div de 1200 x 720.
function pixelOf(minute, price) {
  const width = 1200 - 66 - 18;
  const height = 720 - 16 - 44;
  return {
    x: 66 + (minute - simX[0]) / (simX[1] - simX[0]) * width,
    y: 16 + (simY[1] - price) / (simY[1] - simY[0]) * height,
  };
}

function armar(side) {
  elements['sim-buttons'].children
    .filter(function (button) { return button.dataset.side === side; })
    .forEach(function (button) { button.fire('click'); });
}

function clicGrafico(point) {
  elements['chart'].fire('click', {
    clientX: point.x, clientY: point.y,
    preventDefault() {}, stopPropagation() {},
  });
}

function arrastrarCaja(desde, hasta) {
  elements['chart'].fire('mousedown', {
    clientX: desde.x, clientY: desde.y,
    preventDefault() {}, stopPropagation() {},
  });
  fireDocument('mousemove', {
    clientX: hasta.x, clientY: hasta.y, preventDefault() {},
  });
  fireDocument('mouseup', {});
}

function cajaSimulada() {
  const shapes = plotCalls[plotCalls.length - 1].sim || [];
  const busca = function (name) {
    return shapes.filter(function (shape) { return shape.name === name; })[0];
  };
  const linea = busca('sim-entrada');
  if (!linea) { return null; }
  return {
    entry: linea.y0,
    target: busca('sim-objetivo').y1,
    stop: busca('sim-riesgo').y1,
    from: minuteOf(linea.x0),
    to: minuteOf(linea.x1),
  };
}

function asaDeLaCaja(price) {
  const caja = cajaSimulada();
  return pixelOf(Math.round((caja.from + caja.to) / 2), caja[price]);
}

const entradaSimulada = (simY[0] + simY[1]) / 2;
const minutoSimulado = simX[0] + Math.round((simX[1] - simX[0]) * 0.4);

armar('long');
steps.push(snapshot('sim-armado'));
// Escape suelta el botón sin plantar nada.
pressKey('Escape');
steps.push(snapshot('sim-desarmado'));

armar('long');
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('sim-largo'));

// El borde de fuera de la caja roja mueve el stop y nada más: el objetivo se
// queda donde estaba y el R:R se vuelve a medir solo.
const asaStop = asaDeLaCaja('stop');
arrastrarCaja(asaStop, { x: asaStop.x, y: asaStop.y + 40 });
steps.push(snapshot('sim-stop-arrastrado'));

// La línea de la entrada mueve el conjunto entero: las distancias no cambian.
const asaEntrada = asaDeLaCaja('entry');
arrastrarCaja(asaEntrada, { x: asaEntrada.x, y: asaEntrada.y - 25 });
steps.push(snapshot('sim-entrada-arrastrada'));

// Arrastrar el objetivo cambia el R:R: manda la distancia que se ve.
const asaObjetivo = asaDeLaCaja('target');
arrastrarCaja(asaObjetivo, { x: asaObjetivo.x, y: asaObjetivo.y + 30 });
steps.push(snapshot('sim-objetivo-a-mano'));

// El R:R del panel se mide MIENTRAS se coloca el objetivo, no al soltarlo.
const asaEnVuelo = asaDeLaCaja('target');
elements['chart'].fire('mousedown', {
  clientX: asaEnVuelo.x, clientY: asaEnVuelo.y,
  preventDefault() {}, stopPropagation() {},
});
fireDocument('mousemove', {
  clientX: asaEnVuelo.x, clientY: asaEnVuelo.y - 18, preventDefault() {},
});
const rrEnVuelo = elements['sim-rr'].textContent;
fireDocument('mouseup', {});
steps.push(Object.assign(snapshot('sim-objetivo-en-vuelo'), { simReadoutEnVuelo: rrEnVuelo }));

// En corto el objetivo va por debajo de la entrada y el riesgo por encima.
armar('short');
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('sim-corto'));

elements['sim-clear'].fire('click');
steps.push(snapshot('sim-quitado'));

// --- La cuenta simulada -------------------------------------------------------
//
// Las cajas se plantan y se cobran sin arrastrar nada, así que su R:R es el 1:2
// con el que nacen y las cifras se pueden comprobar a mano: 50 $ al 2 % son
// 1,00 $ de riesgo y 2,00 $ de objetivo.
function apuntar(result) {
  elements['account-buttons'].children
    .filter(function (button) { return button.dataset.result === result; })
    .forEach(function (button) { button.fire('click'); });
}

function plantarCaja(side) {
  armar(side);
  clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
}

steps.push(snapshot('cuenta-intacta'));
plantarCaja('long');
steps.push(snapshot('cuenta-con-caja'));
apuntar('win');
steps.push(snapshot('cuenta-ganada'));
plantarCaja('short');
apuntar('loss');
steps.push(snapshot('cuenta-perdida'));
plantarCaja('long');
apuntar('be');
steps.push(snapshot('cuenta-break-even'));
elements['account-copy'].fire('click');
steps.push(snapshot('cuenta-copiada'));
elements['account-undo'].fire('click');
steps.push(snapshot('cuenta-deshecha'));
elements['sim-clear'].fire('click');
elements['account-initial'].fire('change', { target: { value: '1000' } });
steps.push(snapshot('cuenta-recapitalizada'));
elements['account-mode'].fire('change', { target: { value: 'cash' } });
elements['account-risk'].fire('change', { target: { value: '25' } });
steps.push(snapshot('cuenta-riesgo-fijo'));
elements['account-reset'].fire('click');
steps.push(snapshot('cuenta-reiniciada'));
elements['account-mode'].fire('change', { target: { value: 'percent' } });
elements['account-risk'].fire('change', { target: { value: '2' } });
elements['account-initial'].fire('change', { target: { value: '50' } });

// --- Marcas a mano: los recuadros ---------------------------------------------
const rectKinds = payload.marks.rects;

function armarRect(kind) {
  elements['rect-buttons'].children
    .filter(function (button) { return button.dataset.kind === kind; })
    .forEach(function (button) { button.fire('click'); });
}

function recuadros() {
  return (plotCalls[plotCalls.length - 1].rect || []).map(function (shape) {
    return {
      name: shape.name,
      high: shape.y1,
      low: shape.y0,
      from: minuteOf(shape.x0),
      to: minuteOf(shape.x1),
    };
  });
}

function asaDelRecuadro(index, borde) {
  const rect = recuadros()[index];
  const minuto = Math.round((rect.from + rect.to) / 2);
  if (borde === 'high') { return pixelOf(minuto, rect.high); }
  if (borde === 'low') { return pixelOf(minuto, rect.low); }
  return pixelOf(minuto, (rect.high + rect.low) / 2);
}

steps.push(snapshot('rect-sin-nada'));
armarRect(rectKinds[0]);
steps.push(snapshot('rect-armado'));
// Escape suelta el botón sin plantar nada, igual que en el simulador.
pressKey('Escape');
steps.push(snapshot('rect-desarmado'));

armarRect(rectKinds[0]);
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('rect-plantado'));

// El borde de arriba mueve el techo y nada más.
const asaTecho = asaDelRecuadro(0, 'high');
arrastrarCaja(asaTecho, { x: asaTecho.x, y: asaTecho.y - 20 });
steps.push(snapshot('rect-techo-arrastrado'));

// Por dentro se mueve entero: el alto y el ancho no cambian.
const asaDentro = asaDelRecuadro(0, 'body');
arrastrarCaja(asaDentro, { x: asaDentro.x + 40, y: asaDentro.y + 30 });
steps.push(snapshot('rect-movido'));

// Se plantan varios, y se numeran POR NOMBRE.
if (rectKinds.length > 1) {
  armarRect(rectKinds[1]);
  clicGrafico(pixelOf(minutoSimulado, (simY[1] + entradaSimulada) / 2));
  steps.push(snapshot('rect-segundo'));
}
armarRect(rectKinds[0]);
clicGrafico(pixelOf(minutoSimulado, (simY[0] + entradaSimulada) / 2));
steps.push(snapshot('rect-tercero'));

elements['rect-undo'].fire('click');
steps.push(snapshot('rect-deshecho'));

// --- Marcas a mano: las líneas ------------------------------------------------
const lineKinds = payload.marks.lines;

function armarLinea(kind) {
  elements['line-buttons'].children
    .filter(function (button) { return button.dataset.kind === kind; })
    .forEach(function (button) { button.fire('click'); });
}

function lineas() {
  return (plotCalls[plotCalls.length - 1].line || []).map(function (shape) {
    return {
      name: shape.name,
      left: shape.y0,
      right: shape.y1,
      from: minuteOf(shape.x0),
      to: minuteOf(shape.x1),
    };
  });
}

function asaDeLinea(index, parte) {
  const linea = lineas()[index];
  if (parte === 'left') { return pixelOf(linea.from, linea.left); }
  if (parte === 'right') { return pixelOf(linea.to, linea.right); }
  return pixelOf(
    Math.round((linea.from + linea.to) / 2), (linea.left + linea.right) / 2
  );
}

armarLinea(lineKinds[0]);
steps.push(snapshot('linea-armada'));
pressKey('Escape');
steps.push(snapshot('linea-desarmada'));

armarLinea(lineKinds[0]);
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('linea-plantada'));

// El extremo derecho la INCLINA: sube ese lado y el izquierdo se queda.
const asaDerecha = asaDeLinea(0, 'right');
arrastrarCaja(asaDerecha, { x: asaDerecha.x, y: asaDerecha.y - 30 });
steps.push(snapshot('linea-inclinada'));

// Por dentro se mueve entera: la inclinación no cambia.
const asaTrazo = asaDeLinea(0, 'body');
arrastrarCaja(asaTrazo, { x: asaTrazo.x + 40, y: asaTrazo.y + 30 });
steps.push(snapshot('linea-movida'));

if (lineKinds.length > 1) {
  armarLinea(lineKinds[1]);
  clicGrafico(pixelOf(minutoSimulado, (simY[1] + entradaSimulada) / 2));
  steps.push(snapshot('linea-segunda'));
}

// --- Las marcas son de CADA par ------------------------------------------------
//
// Lo dibujado en un par se guarda al cambiar y vuelve al regresar; en el otro no
// se ve nada de lo del primero.
if (payload.symbols.length > 1) {
  steps.push(snapshot('marcas-en-el-primer-par'));
  selectSymbol(payload.symbols[1].id);
  steps.push(snapshot('marcas-en-el-segundo-par'));
  selectSymbol(payload.symbols[0].id);
  steps.push(snapshot('marcas-de-vuelta'));
}

elements['rect-clear'].fire('click');
elements['line-clear'].fire('click');
steps.push(snapshot('marcas-limpias'));

// --- Auditoría ciega -----------------------------------------------------------
// En H3, donde hay capa calculada que ocultar.
if (payload.symbols[0].charts.indexOf('H3') >= 0) { selectChart('H3'); }
elements['blind-seed'].value = '12345';
elements['blind-seed'].fire('change', {});
elements['blind-start'].fire('click');
const ciega = snapshot('ciega');
steps.push(ciega);
elements['blind-reveal'].fire('click');
steps.push(snapshot('ciega-revelada'));
elements['blind-exit'].fire('click');
steps.push(snapshot('ciega-fuera'));
// La misma semilla reabre la misma ventana.
elements['blind-seed'].value = '12345';
elements['blind-seed'].fire('change', {});
elements['blind-start'].fire('click');
steps.push(snapshot('ciega-repetida'));
elements['blind-exit'].fire('click');

console.log(JSON.stringify({
  unknownElements: missing,
  symbolLabels: symbols.map(function (b) { return b.textContent; }),
  symbolIds: symbols.map(function (b) { return b.dataset.symbol; }),
  symbolTitles: symbols.map(function (b) { return b.title; }),
  chartTabs: tabs().map(function (tab) { return tab.textContent; }),
  chartTitles: tabs().map(function (tab) { return tab.title; }),
  presetLabels: presets.map(function (button) { return button.textContent; }),
  rectLabels: elements['rect-buttons'].children.map(function (b) { return b.textContent; }),
  lineLabels: elements['line-buttons'].children.map(function (b) { return b.textContent; }),
  totalPlots: plotCalls.length,
  steps: steps,
}));
