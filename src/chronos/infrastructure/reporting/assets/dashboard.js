/* Panel interactivo de una corrida de backtest.
 *
 * Todo ocurre en el navegador sobre los datos embebidos: al cambiar el rango de
 * fechas o los filtros se recalculan métricas, gráficos y tabla. No hay servidor
 * ni peticiones de red.
 *
 * Importante: filtrar NO es lo mismo que volver a ejecutar el backtest. El panel
 * muestra cómo se comportó la estrategia dentro de ese tramo, con las decisiones
 * que tomó en la corrida completa. Para simular de verdad otro periodo:
 *     chronos backtest --start 2024-01-01 --end 2024-06-30
 */
(function () {
  'use strict';

  var DATA = window.CHRONOS_DATA;
  var THEME = window.CHRONOS_THEME;
  var MS_PER_DAY = 86400000;
  var MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
  var TABLE_LIMIT = 300;

  var state = {
    from: DATA.curve.times[0],
    to: DATA.curve.times[DATA.curve.times.length - 1],
    sides: new Set(['BUY', 'SELL']),
    reasons: new Set(),
    sortKey: 'tIn',
    sortDir: 1,
  };

  // --- Utilidades ----------------------------------------------------------

  function fmtMoney(value) {
    return value.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' USD';
  }

  function fmtPct(value) {
    return (value * 100).toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' %';
  }

  function fmtNum(value, digits) {
    if (value === null || value === undefined || !isFinite(value)) return '—';
    return value.toLocaleString('es-ES', {
      minimumFractionDigits: digits === undefined ? 2 : digits,
      maximumFractionDigits: digits === undefined ? 2 : digits,
    });
  }

  function fmtDate(ms) {
    var d = new Date(ms);
    return d.toISOString().slice(0, 16).replace('T', ' ');
  }

  function toInputValue(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  // El recorte por fechas y el filtrado viven en metrics.js, que se verifica
  // con node contra los resultados de Python.
  var lowerBound = ChronosMetrics.lowerBound;
  var sliceCurve = ChronosMetrics.sliceCurve;

  function filteredTrades() {
    return ChronosMetrics.filterTrades(DATA.trades, state.from, state.to, state.sides, state.reasons);
  }

  // --- Layout común de Plotly ---------------------------------------------

  function baseLayout(title, height, extra) {
    var axis = {
      gridcolor: THEME.gridline,
      zeroline: false,
      linecolor: THEME.baseline,
      ticks: 'outside',
      tickcolor: THEME.baseline,
      tickfont: { color: THEME.muted, size: 11 },
      automargin: true,
    };
    var layout = {
      title: { text: title, font: { size: 15, color: THEME.ink }, x: 0, xanchor: 'left' },
      paper_bgcolor: THEME.surface,
      plot_bgcolor: THEME.surface,
      font: { family: THEME.font, color: THEME.ink, size: 12 },
      xaxis: Object.assign({}, axis),
      yaxis: Object.assign({}, axis),
      margin: { l: 62, r: 20, t: 46, b: 40 },
      height: height,
      hovermode: 'x unified',
      hoverlabel: {
        bgcolor: THEME.surface,
        bordercolor: THEME.baseline,
        font: { family: THEME.font, color: THEME.ink, size: 12 },
      },
      legend: {
        orientation: 'h', yanchor: 'bottom', y: 1.02, x: 0,
        font: { color: THEME.secondary, size: 12 }, bgcolor: 'rgba(0,0,0,0)',
      },
      showlegend: false,
    };
    return Object.assign(layout, extra || {});
  }

  var PLOT_CONFIG = { displaylogo: false, responsive: true, displayModeBar: 'hover' };

  // --- Gráficos ------------------------------------------------------------

  function drawEquity(curve, initial) {
    var dates = curve.times.map(function (t) { return new Date(t); });
    var traces = [
      {
        x: dates, y: curve.equity, name: 'Equity', type: 'scatter', mode: 'lines',
        line: { color: THEME.series[0], width: 2 },
        hovertemplate: 'Equity: %{y:,.2f}<extra></extra>',
      },
      {
        x: dates, y: curve.balance, name: 'Balance', type: 'scatter', mode: 'lines',
        line: { color: THEME.series[1], width: 2, dash: 'dot' },
        hovertemplate: 'Balance: %{y:,.2f}<extra></extra>',
      },
    ];
    var layout = baseLayout('Curva de capital', 360, {
      showlegend: true,
      yaxis: { title: { text: 'USD', font: { color: THEME.secondary, size: 12 } }, gridcolor: THEME.gridline, tickfont: { color: THEME.muted, size: 11 }, automargin: true },
      shapes: [{
        type: 'line', xref: 'paper', x0: 0, x1: 1, y0: initial, y1: initial,
        line: { color: THEME.muted, width: 1, dash: 'dash' },
      }],
    });
    Plotly.react('chart-equity', traces, layout, PLOT_CONFIG);
  }

  function drawDrawdown(curve, series) {
    var traces = [{
      x: curve.times.map(function (t) { return new Date(t); }),
      y: series,
      type: 'scatter', mode: 'lines', name: 'Drawdown',
      line: { color: THEME.negative, width: 2 },
      fill: 'tozeroy', fillcolor: 'rgba(208, 59, 59, 0.15)',
      hovertemplate: 'Drawdown: %{y:.2f}%<extra></extra>',
    }];
    Plotly.react('chart-drawdown', traces, baseLayout('Drawdown', 240, {
      yaxis: { title: { text: '%', font: { color: THEME.secondary, size: 12 } }, gridcolor: THEME.gridline, tickfont: { color: THEME.muted, size: 11 }, automargin: true },
    }), PLOT_CONFIG);
  }

  function drawPrices(trades) {
    var prices = DATA.prices;
    if (!prices || !prices.times.length) return;

    var start = lowerBound(prices.times, state.from);
    var end = lowerBound(prices.times, state.to + 1);
    var dates = prices.times.slice(start, end).map(function (t) { return new Date(t); });

    var traces = [{
      x: dates, y: prices.close.slice(start, end), name: DATA.meta.symbol,
      type: 'scatter', mode: 'lines', line: { color: THEME.muted, width: 1 },
      hovertemplate: '%{y:,.2f}<extra></extra>',
    }];

    [['BUY', 'Entrada larga', THEME.positive, 'triangle-up'],
     ['SELL', 'Entrada corta', THEME.negative, 'triangle-down']].forEach(function (spec) {
      var subset = trades.filter(function (t) { return t.side === spec[0]; });
      if (!subset.length) return;
      traces.push({
        x: subset.map(function (t) { return new Date(t.tIn); }),
        y: subset.map(function (t) { return t.pIn; }),
        name: spec[1], type: 'scatter', mode: 'markers',
        marker: { color: spec[2], size: 10, symbol: spec[3], line: { color: THEME.surface, width: 2 } },
        text: subset.map(function (t) { return t.reason + ' · ' + fmtNum(t.pnl) + ' USD'; }),
        hovertemplate: '%{x}<br>Entrada: %{y:,.2f}<br>%{text}<extra></extra>',
      });
    });

    Plotly.react('chart-price', traces, baseLayout(
      'Precio y operaciones' + (DATA.meta.pricesDownsampled ? ' (serie submuestreada)' : ''),
      360,
      { showlegend: true, hovermode: 'closest',
        yaxis: { title: { text: 'USD', font: { color: THEME.secondary, size: 12 } }, gridcolor: THEME.gridline, tickfont: { color: THEME.muted, size: 11 }, automargin: true } }
    ), PLOT_CONFIG);
  }

  function drawRDistribution(trades) {
    var values = trades
      .map(function (t) { return t.r; })
      .filter(function (r) { return r !== null && r !== undefined && isFinite(r); });

    var box = document.getElementById('chart-r').parentElement;
    if (values.length < 2) {
      box.style.display = 'none';
      return;
    }
    box.style.display = '';

    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var bins = Math.max(6, Math.min(40, Math.ceil(Math.sqrt(values.length))));
    var width = (max - min) / bins || 1;
    var counts = new Array(bins).fill(0);
    values.forEach(function (v) {
      var idx = Math.min(bins - 1, Math.floor((v - min) / width));
      counts[idx]++;
    });
    var centers = counts.map(function (_, i) { return min + width * (i + 0.5); });

    var traces = [{
      x: centers, y: counts, type: 'bar',
      marker: {
        color: centers.map(function (c) { return c > 0 ? THEME.positive : THEME.negative; }),
        line: { color: THEME.surface, width: 2 },
      },
      width: width * 0.9,
      hovertemplate: 'R ≈ %{x:.2f}<br>Operaciones: %{y}<extra></extra>',
    }];
    Plotly.react('chart-r', traces, baseLayout('Distribución en múltiplos de R', 280, {
      hovermode: 'closest', bargap: 0.05,
      xaxis: { title: { text: 'R (resultado / riesgo inicial)', font: { color: THEME.secondary, size: 12 } }, gridcolor: THEME.gridline, tickfont: { color: THEME.muted, size: 11 }, automargin: true },
      yaxis: { title: { text: 'Operaciones', font: { color: THEME.secondary, size: 12 } }, gridcolor: THEME.gridline, tickfont: { color: THEME.muted, size: 11 }, automargin: true },
    }), PLOT_CONFIG);
  }

  function drawMonthly(curve) {
    var box = document.getElementById('chart-monthly').parentElement;
    if (curve.times.length < 2) { box.style.display = 'none'; return; }

    // Último equity de cada mes natural.
    var keys = [];
    var closes = [];
    var currentKey = null;
    for (var i = 0; i < curve.times.length; i++) {
      var d = new Date(curve.times[i]);
      var key = d.getUTCFullYear() * 12 + d.getUTCMonth();
      if (key !== currentKey) { keys.push(key); closes.push(curve.equity[i]); currentKey = key; }
      else closes[closes.length - 1] = curve.equity[i];
    }
    if (keys.length < 2) { box.style.display = 'none'; return; }
    box.style.display = '';

    var years = [];
    var cells = {};
    for (var j = 0; j < keys.length; j++) {
      var previous = j === 0 ? curve.equity[0] : closes[j - 1];
      var ret = previous ? (closes[j] / previous - 1) * 100 : 0;
      var year = Math.floor(keys[j] / 12);
      var month = keys[j] % 12;
      if (years.indexOf(year) === -1) years.push(year);
      cells[year + ':' + month] = ret;
    }
    years.sort();

    var z = years.map(function (year) {
      return MONTHS.map(function (_, month) {
        var value = cells[year + ':' + month];
        return value === undefined ? null : value;
      });
    });
    var limit = 0;
    z.forEach(function (row) { row.forEach(function (v) { if (v !== null) limit = Math.max(limit, Math.abs(v)); }); });
    limit = limit || 1;

    var traces = [{
      z: z, x: MONTHS, y: years.map(String), type: 'heatmap',
      colorscale: THEME.diverging, zmid: 0, zmin: -limit, zmax: limit,
      xgap: 2, ygap: 2,
      hovertemplate: '%{y} %{x}: %{z:.2f}%<extra></extra>',
      colorbar: { title: { text: '%', side: 'right' }, thickness: 12, outlinewidth: 0 },
    }];
    Plotly.react('chart-monthly', traces, baseLayout('Retorno mensual', 90 + 34 * years.length, {
      hovermode: 'closest',
      yaxis: { autorange: 'reversed', showgrid: false, tickfont: { color: THEME.muted, size: 11 }, automargin: true },
      xaxis: { showgrid: false, tickfont: { color: THEME.muted, size: 11 }, automargin: true },
    }), PLOT_CONFIG);
  }

  // --- Tiles y tabla -------------------------------------------------------

  function renderTiles(m) {
    var tiles = [
      ['Beneficio neto', fmtMoney(m.netProfit), m.netProfit],
      ['Retorno', fmtPct(m.returnPct), m.returnPct],
      ['Drawdown máximo', fmtPct(-m.maxDrawdownPct), -m.maxDrawdownPct],
      ['Profit factor', fmtNum(m.profitFactor), m.profitFactor - 1],
      ['Sharpe', fmtNum(m.sharpe), m.sharpe],
      ['Operaciones', String(m.totalTrades), 0],
      ['Acierto', fmtPct(m.winRate), 0],
      ['Expectativa en R', m.expectancyR === null ? '—' : fmtNum(m.expectancyR, 3), m.expectancyR || 0],
    ];
    document.getElementById('tiles').innerHTML = tiles.map(function (tile) {
      var tone = tile[2] > 0 ? ' positive' : (tile[2] < 0 ? ' negative' : '');
      return '<div class="tile"><span class="tile-label">' + tile[0] + '</span>' +
             '<span class="tile-value' + tone + '">' + tile[1] + '</span></div>';
    }).join('');
  }

  function renderMetricsTable(m) {
    var rows = [
      ['Balance inicial', fmtMoney(m.initialBalance)],
      ['Balance final', fmtMoney(m.finalBalance)],
      ['CAGR', fmtPct(m.cagr)],
      ['Volatilidad anual', fmtPct(m.volatilityAnnual || 0)],
      ['Sortino', fmtNum(m.sortino)],
      ['Calmar', fmtNum(m.calmar)],
      ['Índice de úlcera', fmtNum(m.ulcerIndex)],
      ['Factor de recuperación', fmtNum(m.recoveryFactor)],
      ['Drawdown máximo', fmtMoney(m.maxDrawdown)],
      ['Duración máx. del drawdown', fmtNum(m.maxDrawdownDurationDays, 1) + ' días'],
      ['Ganadoras / perdedoras', m.winners + ' / ' + m.losers],
      ['Beneficio bruto', fmtMoney(m.grossProfit)],
      ['Pérdida bruta', fmtMoney(m.grossLoss)],
      ['Media ganadora', fmtMoney(m.avgWin)],
      ['Media perdedora', fmtMoney(m.avgLoss)],
      ['Ratio de pago', fmtNum(m.payoffRatio)],
      ['Expectativa', fmtMoney(m.expectancy)],
      ['Mayor ganancia', fmtMoney(m.largestWin)],
      ['Mayor pérdida', fmtMoney(m.largestLoss)],
      ['Rachas máx. (ganar / perder)', m.maxConsecutiveWins + ' / ' + m.maxConsecutiveLosses],
      ['Barras medias en mercado', fmtNum(m.avgBarsHeld, 1)],
      ['Exposición', fmtPct(m.exposurePct)],
      ['Comisiones', fmtMoney(m.totalCommission)],
      ['Swap', fmtMoney(m.totalSwap)],
      ['Duración del tramo', fmtNum(m.days, 1) + ' días'],
    ];
    document.getElementById('metrics-table').innerHTML =
      '<table class="metrics"><tbody>' +
      rows.map(function (r) { return '<tr><th>' + r[0] + '</th><td>' + r[1] + '</td></tr>'; }).join('') +
      '</tbody></table>';
  }

  var COLUMNS = [
    ['id', '#', function (t) { return t.id; }],
    ['side', 'Lado', function (t) { return t.side === 'BUY' ? 'Largo' : 'Corto'; }],
    ['volume', 'Lotes', function (t) { return fmtNum(t.volume); }],
    ['tIn', 'Entrada', function (t) { return fmtDate(t.tIn); }],
    ['pIn', 'Precio', function (t) { return fmtNum(t.pIn); }],
    ['tOut', 'Salida', function (t) { return fmtDate(t.tOut); }],
    ['pOut', 'Precio', function (t) { return fmtNum(t.pOut); }],
    ['reason', 'Motivo', function (t) { return t.reason; }],
    ['pnl', 'Resultado', function (t) { return fmtNum(t.pnl); }],
    ['r', 'R', function (t) { return t.r === null ? '—' : fmtNum(t.r, 2); }],
  ];

  function renderTradesTable(trades) {
    var target = document.getElementById('trades-table');
    if (!trades.length) {
      target.innerHTML = '<p class="muted">Ninguna operación en el tramo seleccionado.</p>';
      return;
    }

    var sorted = trades.slice().sort(function (a, b) {
      var x = a[state.sortKey];
      var y = b[state.sortKey];
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x > y ? 1 : -1) * state.sortDir;
    });
    var visible = sorted.slice(0, TABLE_LIMIT);

    var head = COLUMNS.map(function (col) {
      var arrow = state.sortKey === col[0] ? (state.sortDir === 1 ? ' ▲' : ' ▼') : '';
      return '<th data-key="' + col[0] + '">' + col[1] + arrow + '</th>';
    }).join('');

    var body = visible.map(function (t) {
      var tone = t.pnl > 0 ? 'positive' : 'negative';
      return '<tr>' + COLUMNS.map(function (col) {
        var css = (col[0] === 'pnl' || col[0] === 'r') ? ' class="' + tone + '"' : '';
        return '<td' + css + '>' + col[2](t) + '</td>';
      }).join('') + '</tr>';
    }).join('');

    var note = trades.length > TABLE_LIMIT
      ? '<p class="muted">Mostrando ' + TABLE_LIMIT + ' de ' + trades.length +
        ' operaciones. El detalle completo está en <code>trades.csv</code>.</p>'
      : '';

    target.innerHTML = '<table class="trades"><thead><tr>' + head + '</tr></thead><tbody>' +
                       body + '</tbody></table>' + note;

    target.querySelectorAll('th').forEach(function (th) {
      th.addEventListener('click', function () {
        var key = th.getAttribute('data-key');
        state.sortDir = state.sortKey === key ? -state.sortDir : 1;
        state.sortKey = key;
        render();
      });
    });
  }

  // --- Ciclo de render -----------------------------------------------------

  function render() {
    var curve = sliceCurve(DATA.curve, state.from, state.to);
    var trades = filteredTrades();
    var base = curve.startIndex > 0
      ? DATA.curve.equity[curve.startIndex - 1]
      : DATA.meta.initialBalance;

    var metrics = ChronosMetrics.compute(curve, trades, { initialBalance: base });

    renderTiles(metrics);
    renderMetricsTable(metrics);
    renderTradesTable(trades);
    drawEquity(curve, base);
    drawDrawdown(curve, metrics.drawdownSeries);
    drawMonthly(curve);
    drawRDistribution(trades);
    drawPrices(trades);

    document.getElementById('range-label').textContent =
      fmtDate(state.from) + ' → ' + fmtDate(state.to) +
      '  ·  ' + trades.length + ' operaciones';
  }

  // --- Controles -----------------------------------------------------------

  function applyPreset(preset) {
    var last = DATA.curve.times[DATA.curve.times.length - 1];
    var first = DATA.curve.times[0];
    var from = first;
    if (preset === 'ytd') {
      var year = new Date(last).getUTCFullYear();
      from = Date.UTC(year, 0, 1);
    } else if (preset !== 'all') {
      var days = { '1m': 30, '3m': 90, '6m': 182, '1y': 365 }[preset];
      from = last - days * MS_PER_DAY;
    }
    state.from = Math.max(first, from);
    state.to = last;
    syncInputs();
    render();
  }

  function syncInputs() {
    document.getElementById('from').value = toInputValue(state.from);
    document.getElementById('to').value = toInputValue(state.to);
  }

  function setupControls() {
    var first = DATA.curve.times[0];
    var last = DATA.curve.times[DATA.curve.times.length - 1];

    ['from', 'to'].forEach(function (id) {
      var input = document.getElementById(id);
      input.min = toInputValue(first);
      input.max = toInputValue(last);
      input.addEventListener('change', function () {
        if (!input.value) return;
        var ms = Date.parse(input.value + 'T00:00:00Z');
        if (id === 'from') state.from = Math.max(first, Math.min(ms, state.to));
        else state.to = Math.min(last, Math.max(ms + MS_PER_DAY - 1, state.from));
        syncInputs();
        render();
      });
    });

    document.querySelectorAll('[data-preset]').forEach(function (button) {
      button.addEventListener('click', function () {
        document.querySelectorAll('[data-preset]').forEach(function (b) { b.classList.remove('active'); });
        button.classList.add('active');
        applyPreset(button.getAttribute('data-preset'));
      });
    });

    document.querySelectorAll('[data-side]').forEach(function (box) {
      box.addEventListener('change', function () {
        if (box.checked) state.sides.add(box.getAttribute('data-side'));
        else state.sides.delete(box.getAttribute('data-side'));
        render();
      });
    });

    var reasons = {};
    DATA.trades.forEach(function (t) { reasons[t.reason] = (reasons[t.reason] || 0) + 1; });
    var container = document.getElementById('reason-filters');
    container.innerHTML = Object.keys(reasons).sort().map(function (reason) {
      return '<label class="chip"><input type="checkbox" data-reason="' + reason + '"> ' +
             reason + ' <span class="count">' + reasons[reason] + '</span></label>';
    }).join('');
    container.querySelectorAll('[data-reason]').forEach(function (box) {
      box.addEventListener('change', function () {
        var reason = box.getAttribute('data-reason');
        if (box.checked) state.reasons.add(reason);
        else state.reasons.delete(reason);
        render();
      });
    });

    document.getElementById('reset').addEventListener('click', function () {
      state.from = first;
      state.to = last;
      state.sides = new Set(['BUY', 'SELL']);
      state.reasons = new Set();
      document.querySelectorAll('[data-side]').forEach(function (b) { b.checked = true; });
      document.querySelectorAll('[data-reason]').forEach(function (b) { b.checked = false; });
      document.querySelectorAll('[data-preset]').forEach(function (b) { b.classList.remove('active'); });
      document.querySelector('[data-preset="all"]').classList.add('active');
      syncInputs();
      render();
    });
  }

  syncInputs();
  setupControls();
  render();
})();
