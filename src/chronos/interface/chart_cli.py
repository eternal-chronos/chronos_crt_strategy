"""CLI del explorador de velas.

Punto de composición: aquí se juntan configuración, carga de bid/ask,
verificación de zona horaria, agregación M1 → M15/H1/H4/D, las capas CRT
—rangos diarios, rangos H4 y la alineación entre los dos— y el HTML.

Este módulo no detecta nada: la regla vive en `domain/crt` y se compone en
`interface/crt_layer`. Aquí sólo se llama, se imprime y se le pasa al dibujo lo
que ya está calculado.
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
from chronos.domain.crt.alignment import MODES, STATES
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
from chronos.interface.crt_layer import (
    DEFAULT_ALIGNMENT,
    DEFAULT_PARAMS,
    FOCUS_TIMEFRAME,
    RANGE_TIMEFRAME,
    SymbolRanges,
    alignment_layer,
    alignment_summary,
    alignment_waiting,
    build_ranges,
    range_layer,
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
        # Antes que nada: un día de mercado que falte no da error, da un rango
        # detectado contra la vela equivocada.
        ranges = build_ranges(run)
        _print_coverage(ranges)
        _audit_or_stop(run, run_config, skip_tz_audit)

        layer = range_layer(ranges)
        focus = alignment_layer(ranges)
        destination = out or (Path(run_config.reporting.output_dir) / EXPLORER_FILE)
        destination.parent.mkdir(parents=True, exist_ok=True)
        html = render_explorer(run, ranges=layer, alignment=focus)
        destination.write_text(html, encoding="utf-8")

        payload = build_payload(run, ranges=layer, alignment=focus)
        _print_ranges(ranges)
        _print_alignment(ranges)
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


# --- La capa CRT --------------------------------------------------------------

#: Cuántos días que faltan se nombran uno a uno antes de resumir por años.
_MISSING_SHOWN = 8


def _print_coverage(items: tuple[SymbolRanges, ...]) -> None:
    """Los días de mercado que faltan en el histórico diario de cada par.

    Se imprime ANTES de dibujar y antes de contar nada: si falta un día, el
    siguiente se lee contra el anterior y el rango sale de dos velas que en el
    mercado no fueron consecutivas.
    """
    table = Table(box=None, pad_edge=False)
    table.add_column("Par", style="dim")
    table.add_column("Sesiones", justify="right")
    table.add_column("Esperadas", justify="right")
    table.add_column("Faltan", justify="right")
    table.add_column("Tramo")
    for item in items:
        coverage = item.coverage
        faltan = len(coverage.missing)
        table.add_row(
            item.name,
            f"{coverage.present:,}",
            f"{coverage.expected:,}",
            "—" if not faltan else f"[yellow]{faltan:,}[/yellow]",
            "—"
            if coverage.first is None
            else f"{coverage.first:%Y-%m-%d} → {coverage.last:%Y-%m-%d}",
        )
    console.print("\n[bold]Días de mercado en el histórico diario[/bold]")
    console.print(table)

    for item in items:
        missing = item.coverage.missing
        if not missing:
            continue
        nombres = ", ".join(f"{moment:%Y-%m-%d}" for moment in missing[:_MISSING_SHOWN])
        resto = (
            ""
            if len(missing) <= _MISSING_SHOWN
            else " · por año: "
            + ", ".join(
                f"{year}: {count}" for year, count in sorted(item.coverage.missing_by_year().items())
            )
        )
        console.print(
            f"[yellow]Faltan[/yellow] {len(missing):,} sesiones en {item.name}: "
            f"{nombres}{'…' if len(missing) > _MISSING_SHOWN else ''}{resto}\n"
            "[dim]Los festivos salen aquí igual que una descarga incompleta: quien mira "
            "sabe qué día fue Navidad.[/dim]"
        )


def _print_ranges(items: tuple[SymbolRanges, ...]) -> None:
    """Cuántos rangos hay, qué fue de ellos y hacia dónde apunta el último vivo."""
    table = Table(box=None, pad_edge=False)
    table.add_column("Par", style="dim")
    table.add_column("Rangos", justify="right")
    table.add_column("Alcistas", justify="right")
    table.add_column("Bajistas", justify="right")
    table.add_column("Completados", justify="right")
    table.add_column("Fallidos", justify="right")
    table.add_column("Velas", justify="right")
    table.add_column("Vivos", justify="right")
    for item in items:
        resumen = item.summary
        table.add_row(
            item.name,
            f"{resumen.total:,}",
            f"{resumen.bullish:,}",
            f"{resumen.bearish:,}",
            _share(resumen.completed, resumen.completed_pct),
            _share(resumen.failed, resumen.failed_pct),
            "—"
            if resumen.mean_candles_to_resolve is None
            else f"{resumen.mean_candles_to_resolve:.1f}",
            f"{resumen.active:,}",
        )
    console.print(
        f"\n[bold]Rangos CRT de {TIMEFRAME_LABELS.get(RANGE_TIMEFRAME, RANGE_TIMEFRAME)}"
        f"[/bold] [dim]({DEFAULT_PARAMS.describe()})[/dim]"
    )
    console.print(table)
    console.print(
        "[dim]«Velas» es la media de velas que tardaron en resolverse. Los porcentajes "
        "van sobre los rangos ya RESUELTOS —los vivos no han fallado todavía— y los "
        "expirados cuentan como resueltos.[/dim]"
    )
    for item in items:
        console.print(f"  {item.name}: {_bias_line(item)}")


# --- La capa de enfoque ---------------------------------------------------------


def _print_alignment(items: tuple[SymbolRanges, ...]) -> None:
    """Los rangos H4 y cuánto tiempo pasó el sistema en cada estado."""
    _print_h4_ranges(items)
    for mode in MODES:
        _print_states(items, mode)
    _print_waiting(items)


def _print_h4_ranges(items: tuple[SymbolRanges, ...]) -> None:
    """El mismo recuento que en el diario, sobre las velas H4.

    Es el MISMO detector y el mismo ciclo de vida: lo que cambia es para qué se
    leen. Los diarios dan el sesgo; estos dicen cuándo el precio va enfocado.
    """
    table = Table(box=None, pad_edge=False)
    table.add_column("Par", style="dim")
    table.add_column("Rangos", justify="right")
    table.add_column("Alcistas", justify="right")
    table.add_column("Bajistas", justify="right")
    table.add_column("Completados", justify="right")
    table.add_column("Fallidos", justify="right")
    table.add_column("Velas", justify="right")
    table.add_column("Vivos", justify="right")
    for item in items:
        resumen = item.h4_summary
        table.add_row(
            item.name,
            f"{resumen.total:,}",
            f"{resumen.bullish:,}",
            f"{resumen.bearish:,}",
            _share(resumen.completed, resumen.completed_pct),
            _share(resumen.failed, resumen.failed_pct),
            "—"
            if resumen.mean_candles_to_resolve is None
            else f"{resumen.mean_candles_to_resolve:.1f}",
            f"{resumen.active:,}",
        )
    console.print(
        f"\n[bold]Rangos CRT de {TIMEFRAME_LABELS.get(FOCUS_TIMEFRAME, FOCUS_TIMEFRAME)}"
        f"[/bold] [dim]({DEFAULT_PARAMS.describe()} · "
        f"{DEFAULT_ALIGNMENT.describe().split(' · ')[-1]})[/dim]"
    )
    console.print(table)


def _print_states(items: tuple[SymbolRanges, ...], mode: str) -> None:
    """Qué porcentaje del tiempo pasó el sistema en cada estado, en ese modo.

    El tiempo se mide en VELAS H4, que es el reloj con el que se reevalúa: cada
    fila es un cierre de vela. Los dos modos se imprimen juntos a propósito, que
    es la comparación que hay que poder hacer de un vistazo.
    """
    table = Table(box=None, pad_edge=False)
    table.add_column("Par", style="dim")
    table.add_column("Velas H4", justify="right")
    for state in STATES:
        table.add_column(state.replace("_", " ").title(), justify="right")
    for item in items:
        resumen = alignment_summary(item, mode)
        table.add_row(
            item.name,
            f"{resumen.total:,}",
            *(
                f"{resumen.share(state):.1f} % ({resumen.counts.get(state, 0):,})"
                for state in STATES
            ),
        )
    console.print(f"\n[bold]Alineación H4 · modo {mode}[/bold]")
    console.print(table)


def _print_waiting(items: tuple[SymbolRanges, ...]) -> None:
    """Cuánto esperó cada rango diario COMPLETADO a que H4 se pusiera a favor.

    Es la pregunta de si el filtro llega tarde: un rango que llegó a su objetivo
    mientras el sistema seguía en TARGET DEFINIDO es una oportunidad que la
    alineación dejó pasar entera.
    """
    table = Table(box=None, pad_edge=False)
    table.add_column("Par", style="dim")
    table.add_column("Modo", style="dim")
    table.add_column("Rangos D1 completados", justify="right")
    table.add_column("Se alinearon", justify="right")
    table.add_column("Nunca", justify="right")
    table.add_column("Velas H4 de espera (media)", justify="right")
    table.add_column("Mediana", justify="right")
    for item in items:
        for position, mode in enumerate(MODES):
            espera = alignment_waiting(item, mode)
            table.add_row(
                item.name if position == 0 else "",
                mode,
                f"{espera.total:,}",
                f"{len(espera.aligned):,}",
                "—" if not espera.never else f"[yellow]{espera.never:,}[/yellow]",
                "—" if espera.mean_bars is None else f"{espera.mean_bars:.1f}",
                "—" if espera.median_bars is None else f"{espera.median_bars:.0f}",
            )
    console.print("\n[bold]Espera hasta la primera vela alineada[/bold]")
    console.print(table)
    console.print(
        "[dim]Se cuentan sólo los rangos diarios que llegaron a su objetivo y que "
        "pasaron por TARGET DEFINIDO: son los que había que haber operado. «Nunca» "
        "son los que se completaron sin que H4 llegase a ponerse a favor.[/dim]"
    )


def _share(count: int, percent: float | None) -> str:
    return f"{count:,}" if percent is None else f"{count:,} ({percent:.0f} %)"


def _bias_line(item: SymbolRanges) -> str:
    """Hacia dónde va el precio a la última vela cerrada de ese par."""
    bias = item.bias
    cuando = "" if item.as_of is None else f" a {item.as_of:%Y-%m-%d %H:%M} UTC"
    if "target" not in bias:
        return f"[dim]sesgo SIN TARGET{cuando}: ningún rango vivo[/dim]"
    return (
        f"sesgo [bold]{str(bias['direction']).upper()}[/bold]{cuando} · "
        f"target {bias['target']:,.{item.decimals}f} · "
        f"invalidación {bias['invalidation']:,.{item.decimals}f} "
        f"[dim](rango nº {bias['range_id']})[/dim]"
    )


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
