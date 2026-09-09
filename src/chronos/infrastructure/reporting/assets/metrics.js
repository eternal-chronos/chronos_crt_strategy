/* Métricas recalculadas en el navegador sobre el subrango seleccionado.
 *
 * Estas fórmulas replican las de `chronos.application.metrics.performance`.
 * La equivalencia no es un acto de fe: `tests/infrastructure/test_dashboard.py`
 * ejecuta este fichero con node sobre una corrida real y compara los resultados
 * contra los de Python. Si tocas una fórmula aquí, ese test lo detecta.
 */
(function (root) {
  'use strict';

  var TRADING_DAYS_PER_YEAR = 252;
  var MS_PER_DAY = 86400000;

  function mean(values) {
    if (!values.length) return 0;
    var total = 0;
    for (var i = 0; i < values.length; i++) total += values[i];
    return total / values.length;
  }

  function stdev(values, ddof) {
    if (values.length <= ddof) return 0;
    var m = mean(values);
    var acc = 0;
    for (var i = 0; i < values.length; i++) acc += (values[i] - m) * (values[i] - m);
    return Math.sqrt(acc / (values.length - ddof));
  }

  /* Último valor de equity de cada día natural (equivale a resample('1D').last()). */
  function dailyLast(times, equity) {
    var days = [];
    var values = [];
    var currentDay = null;
    for (var i = 0; i < times.length; i++) {
      var day = Math.floor(times[i] / MS_PER_DAY);
      if (day !== currentDay) {
        days.push(day);
        values.push(equity[i]);
        currentDay = day;
      } else {
        values[values.length - 1] = equity[i];
      }
    }
    return values;
  }

  function dailyReturns(times, equity) {
    var closes = dailyLast(times, equity);
    var returns = [];
    for (var i = 1; i < closes.length; i++) {
      if (closes[i - 1] === 0) continue;
      returns.push(closes[i] / closes[i - 1] - 1);
    }
    return returns;
  }

  function sharpe(returns) {
    if (returns.length < 2) return 0;
    var sd = stdev(returns, 1);
    if (sd === 0) return 0;
    return (mean(returns) / sd) * Math.sqrt(TRADING_DAYS_PER_YEAR);
  }

  function sortino(returns) {
    if (returns.length < 2) return 0;
    var downside = returns.filter(function (r) { return r < 0; });
    if (!downside.length) return 0;
    var squares = downside.map(function (r) { return r * r; });
    var dev = Math.sqrt(mean(squares));
    if (dev === 0) return 0;
    return (mean(returns) / dev) * Math.sqrt(TRADING_DAYS_PER_YEAR);
  }

  /* Drawdown respecto al máximo previo dentro del tramo analizado. */
  function drawdownStats(times, equity) {
    if (!equity.length) return { abs: 0, pct: 0, durationDays: 0, series: [] };
    var peak = equity[0];
    var maxAbs = 0;
    var maxPct = 0;
    var series = new Array(equity.length);
    var longest = 0;
    var start = null;

    for (var i = 0; i < equity.length; i++) {
      if (equity[i] > peak) peak = equity[i];
      var fall = peak - equity[i];
      series[i] = peak > 0 ? -(fall / peak) * 100 : 0;
      if (fall > maxAbs) maxAbs = fall;
      if (peak > 0 && fall / peak > maxPct) maxPct = fall / peak;

      if (equity[i] < peak) {
        if (start === null) start = times[i];
      } else if (start !== null) {
        longest = Math.max(longest, times[i] - start);
        start = null;
      }
    }
    if (start !== null) longest = Math.max(longest, times[times.length - 1] - start);

    return { abs: maxAbs, pct: maxPct, durationDays: longest / MS_PER_DAY, series: series };
  }

  function ulcerIndex(equity) {
    if (!equity.length) return 0;
    var peak = equity[0];
    var squares = [];
    for (var i = 0; i < equity.length; i++) {
      if (equity[i] > peak) peak = equity[i];
      var pct = peak > 0 ? ((equity[i] - peak) / peak) * 100 : 0;
      squares.push(pct * pct);
    }
    return Math.sqrt(mean(squares));
  }

  function maxStreak(flags) {
    var best = 0;
    var current = 0;
    for (var i = 0; i < flags.length; i++) {
      current = flags[i] ? current + 1 : 0;
      if (current > best) best = current;
    }
    return best;
  }

  function tradeStats(trades) {
    var empty = {
      totalTrades: 0, winners: 0, losers: 0, winRate: 0, grossProfit: 0, grossLoss: 0,
      profitFactor: 0, expectancy: 0, expectancyR: null, avgWin: 0, avgLoss: 0,
      payoffRatio: 0, largestWin: 0, largestLoss: 0, maxConsecutiveWins: 0,
      maxConsecutiveLosses: 0, avgBarsHeld: 0, totalCommission: 0, totalSwap: 0,
    };
    if (!trades.length) return empty;

    var wins = [];
    var losses = [];
    var rValues = [];
    var commission = 0;
    var swap = 0;
    var bars = 0;

    for (var i = 0; i < trades.length; i++) {
      var t = trades[i];
      if (t.pnl > 0) wins.push(t.pnl); else losses.push(t.pnl);
      if (t.r !== null && t.r !== undefined) rValues.push(t.r);
      commission += t.commission;
      swap += t.swap;
      bars += t.bars;
    }

    var grossProfit = wins.reduce(function (a, b) { return a + b; }, 0);
    var grossLoss = -losses.reduce(function (a, b) { return a + b; }, 0);
    var avgWin = wins.length ? grossProfit / wins.length : 0;
    var avgLoss = losses.length ? grossLoss / losses.length : 0;
    var pnl = trades.map(function (t) { return t.pnl; });

    return {
      totalTrades: trades.length,
      winners: wins.length,
      losers: losses.length,
      winRate: wins.length / trades.length,
      grossProfit: grossProfit,
      grossLoss: grossLoss,
      profitFactor: grossLoss > 0 ? grossProfit / grossLoss : Infinity,
      expectancy: mean(pnl),
      expectancyR: rValues.length ? mean(rValues) : null,
      avgWin: avgWin,
      avgLoss: avgLoss,
      payoffRatio: avgLoss > 0 ? avgWin / avgLoss : 0,
      largestWin: Math.max.apply(null, pnl),
      largestLoss: Math.min.apply(null, pnl),
      maxConsecutiveWins: maxStreak(pnl.map(function (v) { return v > 0; })),
      maxConsecutiveLosses: maxStreak(pnl.map(function (v) { return v <= 0; })),
      avgBarsHeld: bars / trades.length,
      totalCommission: commission,
      totalSwap: swap,
    };
  }

  /* Primer índice cuyo tiempo es >= target. Los tiempos vienen ordenados. */
  function lowerBound(times, target) {
    var low = 0;
    var high = times.length;
    while (low < high) {
      var mid = (low + high) >> 1;
      if (times[mid] < target) low = mid + 1; else high = mid;
    }
    return low;
  }

  /* Recorta la curva al rango [from, to] (ambos incluidos). */
  function sliceCurve(curve, from, to) {
    var start = lowerBound(curve.times, from);
    var end = lowerBound(curve.times, to + 1);
    return {
      times: curve.times.slice(start, end),
      equity: curve.equity.slice(start, end),
      balance: curve.balance.slice(start, end),
      exposure: curve.exposure.slice(start, end),
      startIndex: start,
    };
  }

  /* Operaciones abiertas dentro del rango que además pasan los filtros.
   * El criterio es la fecha de ENTRADA: una operación pertenece al periodo en
   * que se abrió, aunque se cerrara más tarde. */
  function filterTrades(trades, from, to, sides, reasons) {
    return trades.filter(function (t) {
      if (t.tIn < from || t.tIn > to) return false;
      if (sides && sides.size && !sides.has(t.side)) return false;
      if (reasons && reasons.size && !reasons.has(t.reason)) return false;
      return true;
    });
  }

  /* Métricas del tramo [from, to] de la curva, con las operaciones ya filtradas. */
  function compute(curve, trades, options) {
    var opts = options || {};
    var times = curve.times;
    var equity = curve.equity;
    var exposure = curve.exposure;

    var stats = tradeStats(trades);
    if (!times.length) {
      return Object.assign({}, stats, {
        initialBalance: opts.initialBalance || 0, finalBalance: opts.initialBalance || 0,
        netProfit: 0, returnPct: 0, cagr: 0, maxDrawdown: 0, maxDrawdownPct: 0,
        maxDrawdownDurationDays: 0, sharpe: 0, sortino: 0, calmar: 0, ulcerIndex: 0,
        recoveryFactor: 0, exposurePct: 0, days: 0, drawdownSeries: [],
      });
    }

    var initial = opts.initialBalance;
    var final = equity[equity.length - 1];
    var netProfit = final - initial;
    var days = (times[times.length - 1] - times[0]) / MS_PER_DAY;
    var years = days > 0 ? days / 365.25 : 0;
    var cagr = years > 0 && initial > 0 && final > 0
      ? Math.pow(final / initial, 1 / years) - 1
      : 0;

    var dd = drawdownStats(times, equity);
    var returns = dailyReturns(times, equity);
    var exposed = 0;
    for (var i = 0; i < exposure.length; i++) if (exposure[i] > 0) exposed++;

    return Object.assign({}, stats, {
      initialBalance: initial,
      finalBalance: final,
      netProfit: netProfit,
      returnPct: initial ? netProfit / initial : 0,
      cagr: cagr,
      maxDrawdown: dd.abs,
      maxDrawdownPct: dd.pct,
      maxDrawdownDurationDays: dd.durationDays,
      drawdownSeries: dd.series,
      volatilityAnnual: returns.length > 1 ? stdev(returns, 1) * Math.sqrt(TRADING_DAYS_PER_YEAR) : 0,
      sharpe: sharpe(returns),
      sortino: sortino(returns),
      calmar: dd.pct > 0 ? cagr / dd.pct : 0,
      ulcerIndex: ulcerIndex(equity),
      recoveryFactor: dd.abs > 0 ? netProfit / dd.abs : 0,
      exposurePct: exposure.length ? exposed / exposure.length : 0,
      days: days,
    });
  }

  root.ChronosMetrics = {
    compute: compute,
    lowerBound: lowerBound,
    sliceCurve: sliceCurve,
    filterTrades: filterTrades,
    tradeStats: tradeStats,
    drawdownStats: drawdownStats,
    dailyReturns: dailyReturns,
    sharpe: sharpe,
    sortino: sortino,
    ulcerIndex: ulcerIndex,
    mean: mean,
    stdev: stdev,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
