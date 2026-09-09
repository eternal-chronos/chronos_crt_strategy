"""Tablas ASCII para informes en texto plano.

Los informes de auditoría se leen en un terminal, se pegan en un correo y se
archivan junto a las capturas: texto plano, ancho fijo y sin colores.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def render_table(
    frame: pd.DataFrame,
    *,
    formats: dict[str, str] | None = None,
    empty: str = "(sin datos)",
) -> str:
    """Dibuja un DataFrame como tabla ASCII con las columnas alineadas."""
    if frame.empty:
        return empty
    formats = formats or {}
    headers = [str(column) for column in frame.columns]
    rows = [
        [_cell(value, formats.get(str(column), "")) for column, value in zip(frame.columns, row, strict=True)]
        for row in frame.itertuples(index=False, name=None)
    ]
    return render_rows(headers, rows)


def render_rows(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Dibuja una tabla ya formateada como cadenas."""
    widths = [len(header) for header in headers]
    for row in rows:
        for position, cell in enumerate(row):
            widths[position] = max(widths[position], len(cell))

    def line(cells: Sequence[str], *, header: bool) -> str:
        return "  ".join(
            cell.ljust(widths[position]) if position == 0 or header else cell.rjust(widths[position])
            for position, cell in enumerate(cells)
        ).rstrip()

    separator = "  ".join("-" * width for width in widths)
    body = [line(headers, header=True), separator]
    body.extend(line(row, header=False) for row in rows)
    return "\n".join(body)


def section(title: str, level: int = 1) -> str:
    """Encabezado de sección subrayado."""
    marker = "=" if level == 1 else "-"
    return f"\n{title}\n{marker * len(title)}"


def _cell(value: object, fmt: str) -> str:
    if value is None:
        return "n/d"
    if isinstance(value, float) and pd.isna(value):
        return "n/d"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M")
    if fmt and isinstance(value, int | float):
        return format(value, fmt)
    return str(value)
