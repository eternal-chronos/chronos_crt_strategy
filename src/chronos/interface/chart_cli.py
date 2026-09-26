"""CLI del explorador de velas.

Punto de composición: aquí se juntan configuración, carga de bid/ask,
verificación de zona horaria, agregación M1 → M15/H1/H4/D y el HTML.

No hay ningún comando de estrategia porque todavía no hay estrategia. Cuando la
haya, sus comandos van en su propio módulo y éste sigue haciendo lo que hace:
enseñar las velas.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from chronos.application.chart.config import TIMEFRAME_LABELS, ExplorerConfig
from chronos.application.chart.timezone_audit import TimezoneAudit, audit_timezone
from chronos.domain.errors import DomainError
from chronos.infrastructure.config.loader import ConfigError, load_explorer_config
from chronos.infrastructure.market.chart_run import ChartRun, SymbolBars, build_chart_run
from chronos.infrastructure.market.loader import SidedHistory
from chronos.infrastructure.reporting.explorer import (
    bar_counts,
    build_payload,
    payload_size,
    render_explorer,
)

chart_app = typer.Typer(
    help="Explorador de velas: dibuja el histórico y déjalo listo para marcar.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_CONFIG = Path("config/explorer.yaml")

#: Nombre del fichero que se escribe. Uno solo: los cuatro pares van dentro.
EXPLORER_FILE = "explorador.html"


@chart_app.command("verify-tz")
def verify_timezone(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    symbol: Annotated[
        list[str] | None,
        typer.Option("--symbol", "-s", help="Sólo estos pares; repetible"),
    ] = None,
) -> None:
    """Verifica empíricamente la zona horaria de cada histórico.

    Obligatoria **antes de mirar nada**. Un offset horario equivocado no produce
    ningún error visible: produce velas H4 y diarias desplazadas, y por tanto un
    gráfico distinto al que se ve en la plataforma.

    Dos hechos que no dependen de ninguna convención del bróker: el hueco
    semanal debe caer sábado y domingo UTC, y el rango medio por minuto debe
    tener un pico marcado hacia las 13:30 UTC.
    """
    with _handled():
        run_config = load_explorer_config(config)
        run = build_chart_run(run_config, only=symbol or None)
        _report_unavailable(run)
        failed = False
        for item in run.symbols:
            console.print(f"\n[bold]{item.name}[/bold]")
            audit = audit_timezone(item.history.frame, run_config.timezone_audit)
            _print_audit(audit, item.history)
            failed = failed or not audit.ok
        if failed:
            raise typer.Exit(code=1)


@chart_app.command("info")
def info(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    symbol: Annotated[
        list[str] | None,
        typer.Option("--symbol", "-s", help="Sólo estos pares; repetible"),
    ] = None,
) -> None:
    """Qué hay cargado: pares, temporalidades, velas y tramo de cada una."""
    with _handled():
        run_config = load_explorer_config(config)
        run = build_chart_run(run_config, only=symbol or None)
        _report_unavailable(run)
        _print_summary(run)


@chart_app.command("explorer")
def explorer(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    symbol: Annotated[
        list[str] | None,
        typer.Option("--symbol", "-s", help="Sólo estos pares; repetible"),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Fichero de salida; por defecto, el de la configuración"),
    ] = None,
    skip_tz_audit: Annotated[
        bool,
        typer.Option(
            "--skip-tz-audit",
            help="Dibuja aunque la verificación de zona horaria falle. El HTML no será auditable.",
        ),
    ] = False,
) -> None:
    """Escribe el explorador: un HTML autocontenido con todos los pares dentro."""
    with _handled():
        run_config = load_explorer_config(config)
        run = build_chart_run(run_config, only=symbol or None)
        _report_unavailable(run)
        _print_summary(run)
        _audit_or_stop(run, run_config, skip_tz_audit)

        destination = out or (Path(run_config.reporting.output_dir) / EXPLORER_FILE)
        destination.parent.mkdir(parents=True, exist_ok=True)
        html = render_explorer(run)
        destination.write_text(html, encoding="utf-8")

        payload = build_payload(run)
        console.print(
            f"\n[green]OK[/green] explorador → {destination}\n"
            f"[dim]{len(html) / 1_048_576:.1f} MB de fichero · "
            f"{payload_size(payload) / 1_048_576:.1f} MB de datos embebidos · "
            f"{_bar_line(payload)}[/dim]"
        )
        console.print(
            "[dim]Ábrelo con doble clic. Funciona sin conexión y sin servidor.[/dim]"
        )


# --- Impresión ---------------------------------------------------------------


def _report_unavailable(run: ChartRun) -> None:
    for reason in run.unavailable:
        console.print(f"[yellow]Aviso:[/yellow] no se dibuja {reason}")


def _print_summary(run: ChartRun) -> None:
    table = Table(box=None, pad_edge=False)
    table.add_column("Par", style="dim")
    table.add_column("Lado")
    table.add_column("Gráfico")
    table.add_column("Velas", justify="right")
    table.add_column("Desde → hasta")
    for item in run.symbols:
        for position, (timeframe, frame) in enumerate(item.frames.items()):
            index = frame.index
            table.add_row(
                item.name if position == 0 else "",
                item.history.side if position == 0 else "",
                TIMEFRAME_LABELS.get(timeframe, timeframe),
                f"{len(frame):,}",
                f"{index[0]} → {index[-1]}",
            )
    console.print()
    console.print(table)
    for item in run.symbols:
        for note in item.skipped:
            console.print(f"[yellow]Aviso:[/yellow] {item.name} · {note}")


def _bar_line(payload: dict[str, Any]) -> str:
    counts = bar_counts(payload)
    return " · ".join(
        f"{symbol}: {sum(charts.values()):,} velas" for symbol, charts in counts.items()
    )


def _audit_or_stop(run: ChartRun, config: ExplorerConfig, skip: bool) -> None:
    if not config.timezone_audit.enabled:
        console.print(
            "[yellow]Verificación de zona horaria desactivada por configuración. "
            "Un offset equivocado no da error: da velas desplazadas.[/yellow]"
        )
        return

    failed: list[SymbolBars] = []
    for item in run.symbols:
        audit = audit_timezone(item.history.frame, config.timezone_audit)
        if audit.ok:
            continue
        failed.append(item)
        console.print(f"\n[bold]{item.name}[/bold]")
        _print_audit(audit, item.history)

    if not failed:
        return

    if not skip:
        console.print(
            "\n[bold red]NO SE DIBUJA.[/bold red] La verificación de zona horaria ha "
            "fallado en: " + ", ".join(item.name for item in failed) + ".\n"
            "Un offset horario equivocado no produce un error visible: produce velas H4 "
            "y diarias\ndesplazadas, y por tanto un gráfico distinto al de tu plataforma. "
            "Corrige el histórico\nantes de seguir, o repite con --skip-tz-audit si sabes "
            "lo que estás mirando."
        )
        raise typer.Exit(code=1)

    console.print(
        "\n[bold yellow]AVISO:[/bold yellow] se continúa con --skip-tz-audit pese al fallo. "
        "Las velas de este explorador NO son auditables contra la plataforma."
    )


def _print_audit(audit: TimezoneAudit, history: SidedHistory) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim")
    table.add_column(justify="right")
    table.add_row("Origen", history.provenance)
    table.add_row("Lado del precio", history.side)
    table.add_row(
        f"Barras del histórico ({audit.resolution_minutes} min)", f"{audit.bars:,}"
    )
    table.add_row("Rango", f"{audit.first_bar} → {audit.last_bar}")
    table.add_row("Paradas > 12 h", f"{audit.gaps_found:,}")
    table.add_row(
        "Empiezan viernes / terminan domingo",
        f"{audit.gaps_starting_friday:,} / {audit.gaps_ending_sunday:,}",
    )
    table.add_row(
        "Pico de volatilidad",
        f"{audit.peak_minute_utc} UTC (esperado {audit.expected_peak_utc}, "
        f"desviación {audit.peak_offset_minutes} min)",
    )
    console.print()
    console.print(table)
    if audit.sample_gaps:
        gap = audit.sample_gaps[0]
        console.print(
            f"[dim]Ejemplo de parada: último {gap.last_before} → primero {gap.first_after}[/dim]"
        )
    verdict = "[green]OK[/green]" if audit.ok else "[bold red]FALLO[/bold red]"
    console.print(f"Verificación de zona horaria: {verdict}")
    for problem in audit.problems:
        console.print(f"  [red]·[/red] {problem}")


@contextmanager
def _handled() -> Iterator[None]:
    """Traduce los errores del dominio y de configuración en salida limpia."""
    try:
        yield
    except (ConfigError, DomainError) as error:
        console.print(f"[bold red]Error:[/bold red] {error}")
        raise typer.Exit(code=1) from error
