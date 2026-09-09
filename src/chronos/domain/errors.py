"""Errores de dominio. Explícitos y tipados: nunca `None` ambiguo."""

from __future__ import annotations


class DomainError(Exception):
    """Raíz de todos los errores de reglas de negocio."""


class InvalidInstrumentSpec(DomainError):
    """La especificación del símbolo es inconsistente o incompleta."""


class InvalidPrice(DomainError):
    """Precio fuera de rango o no representable con el tick del símbolo."""


class InvalidVolume(DomainError):
    """Volumen fuera de los límites del símbolo o no múltiplo del lot step."""


class InvalidOrder(DomainError):
    """La orden viola una invariante (SL del lado equivocado, TP igual a entrada...)."""


class InsufficientMargin(DomainError):
    """El margen libre no cubre la posición solicitada."""


class PositionAlreadyClosed(DomainError):
    """Se intentó operar sobre una posición ya cerrada."""


class RiskLimitBreached(DomainError):
    """Se superó un límite de riesgo configurado (DD máximo, pérdida diaria...)."""


class StrategyError(DomainError):
    """La estrategia produjo un resultado inválido."""
