"""Registro de estrategias.

Cada fase del desarrollo añadirá su estrategia aquí con `@register`. El motor
solo conoce el nombre; nada del núcleo depende de una implementación concreta.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any, TypeVar

from chronos.domain.errors import StrategyError
from chronos.domain.strategy import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}

T = TypeVar("T", bound=Strategy)


def register(name: str) -> Callable[[type[T]], type[T]]:
    """Decorador que da de alta una estrategia bajo un nombre único."""

    def decorator(cls: type[T]) -> type[T]:
        key = name.strip().lower()
        if key in _REGISTRY and _REGISTRY[key] is not cls:
            raise StrategyError(f"Ya existe una estrategia registrada como '{key}'")
        cls.name = key
        _REGISTRY[key] = cls
        return cls

    return decorator


def create_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    """Instancia una estrategia registrada con sus parámetros."""
    discover()
    key = name.strip().lower()
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "ninguna"
        raise StrategyError(f"Estrategia desconocida '{name}'. Disponibles: {available}")
    return _REGISTRY[key](**(params or {}))


def available_strategies() -> list[str]:
    discover()
    return sorted(_REGISTRY)


def discover() -> None:
    """Importa todos los módulos del paquete para que se auto-registren."""
    package = importlib.import_module("chronos.domain.strategies")
    for module in pkgutil.iter_modules(package.__path__):
        if module.name in ("registry", "indicators", "base"):
            continue
        importlib.import_module(f"chronos.domain.strategies.{module.name}")
