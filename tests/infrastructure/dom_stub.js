/* Arranca dashboard.js contra un DOM mínimo para comprobar que el panel se
 * dibuja sin errores: qué elementos toca, qué gráficos pide y qué escribe.
 *
 * No sustituye a mirar el panel en un navegador, pero sí detecta lo que más
 * suele romperse: identificadores que no existen, campos mal nombrados en el
 * payload y excepciones dentro del ciclo de render.
 *
 * Uso: node dom_stub.js <metrics.js> <dashboard.js> <payload.json> <theme.json>
 */
const fs = require('fs');

const [, , metricsPath, dashboardPath, payloadPath, themePath] = process.argv;

const written = {};
const plotCalls = [];
const missing = [];

function makeElement(id) {
  const element = {
    id: id,
    _html: '',
    value: '',
    min: '',
    max: '',
    checked: true,
    textContent: '',
    style: {},
    classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener() {},
    getAttribute() { return null; },
    querySelectorAll() { return []; },
    get innerHTML() { return this._html; },
    set innerHTML(value) { this._html = value; written[this.id] = value; },
  };
  element.parentElement = {
    style: {},
    classList: element.classList,
    addEventListener() {},
    querySelectorAll() { return []; },
  };
  return element;
}

const elements = {};
global.document = {
  getElementById(id) {
    if (!elements[id]) {
      elements[id] = makeElement(id);
      missing.push(id);
    }
    return elements[id];
  },
  querySelector(selector) { return makeElement(selector); },
  querySelectorAll() { return []; },
};

// Elementos que la plantilla HTML declara de verdad.
['tiles', 'metrics-table', 'trades-table', 'range-label', 'from', 'to', 'reset',
 'reason-filters', 'chart-equity', 'chart-drawdown', 'chart-price', 'chart-monthly',
 'chart-r'].forEach(function (id) {
  elements[id] = makeElement(id);
});

global.Plotly = {
  react(target, traces, layout) {
    plotCalls.push({
      target: target,
      traces: traces.length,
      points: traces.reduce(function (total, t) { return total + ((t.x && t.x.length) || 0); }, 0),
      title: layout.title && layout.title.text,
    });
  },
};

global.window = global;
window.CHRONOS_DATA = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
window.CHRONOS_THEME = JSON.parse(fs.readFileSync(themePath, 'utf8'));

eval(fs.readFileSync(metricsPath, 'utf8'));
eval(fs.readFileSync(dashboardPath, 'utf8'));

console.log(JSON.stringify({
  plots: plotCalls,
  tilesHtml: written['tiles'] || '',
  metricsHtml: written['metrics-table'] || '',
  tradesHtml: written['trades-table'] || '',
  rangeLabel: elements['range-label'].textContent,
  unknownElements: missing,
}));
