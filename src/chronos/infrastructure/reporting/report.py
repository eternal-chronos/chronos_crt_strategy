"""Artefactos de una corrida: CSV, JSON y el panel interactivo."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from chronos.application.ports import Clock
from chronos.application.run_backtest import BacktestRun
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting.dashboard import render_dashboard


class ReportWriter:
    """Escribe todo lo que produce una corrida en una carpeta con marca de tiempo.

    Cada carpeta es autocontenida: el panel HTML no necesita red ni los CSV, y los
    CSV/JSON permiten reanalizar la corrida desde pandas sin volver a simular.
    """

    def __init__(self, output_dir: str | Path = "reports", clock: Clock | None = None) -> None:
        self._root = Path(output_dir)
        self._clock: Clock = clock or SystemClock()

    def write(
        self,
        run: BacktestRun,
        *,
        prices: pd.DataFrame | None = None,
        run_id: str | None = None,
    ) -> Path:
        generated_at = self._clock.now()
        stamp = run_id or generated_at.strftime("%Y%m%d_%H%M%S")
        strategy_name = str(run.result.strategy.get("name", "strategy"))
        folder = self._root / f"{stamp}_{strategy_name}"
        folder.mkdir(parents=True, exist_ok=True)

        reporting = run.config.reporting
        if reporting.save_trades_csv:
            run.result.trades_frame().to_csv(folder / "trades.csv", index=False)
        if reporting.save_equity_csv:
            run.result.equity_curve.to_csv(folder / "equity.csv")

        (folder / "metrics.json").write_text(
            json.dumps(run.performance.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (folder / "run.json").write_text(
            json.dumps(
                {
                    "strategy": run.result.strategy,
                    "symbol": run.result.symbol,
                    "timeframe": run.result.timeframe.value,
                    "bars_processed": run.result.bars_processed,
                    "halted_reason": run.result.halted_reason,
                    "rejections": run.result.rejections,
                    "config": asdict(run.config),
                    "instrument": asdict(run.spec),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

        if reporting.html_report:
            html = render_dashboard(run, prices, reporting.max_price_bars, generated_at)
            (folder / "report.html").write_text(html, encoding="utf-8")

        return folder
