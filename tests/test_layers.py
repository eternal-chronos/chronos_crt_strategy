"""La dirección de las dependencias, comprobada en vez de prometida.

`infrastructure → application → domain`. Nunca al revés. Y `domain/` sin red,
disco ni reloj: si algo de eso se cuela, la misma estrategia deja de correr
igual en backtest, paper y live.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "chronos"

#: Qué capas puede importar cada capa, además de sí misma.
ALLOWED = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "interface": {"domain", "application", "infrastructure"},
}

#: Módulos de la biblioteca estándar que atan el dominio al mundo exterior.
FORBIDDEN_IN_DOMAIN = {"pathlib", "os", "socket", "urllib", "http", "sqlite3", "requests", "httpx"}


def _modules(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_las_dependencias_apuntan_hacia_adentro(layer: str) -> None:
    permitidas = ALLOWED[layer] | {layer}
    for path in _modules(layer):
        for name in _imports(path):
            if not name.startswith("chronos."):
                continue
            importada = name.split(".")[1]
            assert importada in permitidas, (
                f"{path.relative_to(SRC)} importa {name}: "
                f"{layer} no puede depender de {importada}"
            )


def test_el_dominio_no_toca_red_ni_disco_ni_reloj() -> None:
    for path in _modules("domain"):
        nombres = {name.split(".")[0] for name in _imports(path)}
        prohibidos = nombres & FORBIDDEN_IN_DOMAIN
        assert not prohibidos, f"{path.relative_to(SRC)} importa {', '.join(sorted(prohibidos))}"

        fuente = path.read_text(encoding="utf-8")
        assert "datetime.now(" not in fuente, f"{path.relative_to(SRC)} llama a datetime.now()"
        assert "Timestamp.now(" not in fuente, f"{path.relative_to(SRC)} llama a Timestamp.now()"


def test_el_explorador_no_sabe_que_existe_ninguna_estrategia() -> None:
    """El explorador es un CHASIS: dibuja velas y de ellas no decide nada.

    Es la promesa del proyecto mientras no haya estrategia, y también después: el
    día que se escriba una, sus capas se le pasan al explorador desde el punto de
    composición —nunca importándolas desde el dibujo, que es como una capa que
    dibuja acaba decidiendo—.
    """
    dibujo = [
        SRC / "infrastructure" / "reporting" / "explorer.py",
        *_modules("application/chart"),
        *sorted((SRC / "infrastructure" / "market").rglob("*.py")),
    ]
    for path in dibujo:
        for name in _imports(path):
            assert "strateg" not in name, (
                f"{path.relative_to(SRC)} importa {name}: el dibujo y la carga de "
                "velas no pueden depender de ninguna estrategia"
            )


def test_la_carga_de_velas_no_depende_del_motor_de_backtest() -> None:
    """Dibujar el histórico y simular una cuenta son dos caminos separados."""
    for path in sorted((SRC / "infrastructure" / "market").rglob("*.py")):
        for name in _imports(path):
            assert "backtest" not in name, f"{path.relative_to(SRC)} importa {name}"
