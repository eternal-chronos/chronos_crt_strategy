"""La aritmética del instrumento: si esto falla, todo lo demás miente."""

from __future__ import annotations

import pytest

from chronos.domain.enums import Side
from chronos.domain.errors import InvalidInstrumentSpec, InvalidVolume
from chronos.domain.instrument import CostModel, InstrumentSpec, SwapModel


def test_valor_del_punto_por_lote(spec: InstrumentSpec) -> None:
    # 100 onzas por lote * 0.01 de tick = 1 USD por tick y lote
    assert spec.value_per_point_per_lot == pytest.approx(1.0)


def test_pnl_bruto_de_un_largo(spec: InstrumentSpec) -> None:
    # 1 lote, 10 USD de recorrido a favor -> 100 oz * 10 USD = 1000 USD
    assert spec.gross_pnl(Side.BUY, 2000.0, 2010.0, 1.0) == pytest.approx(1000.0)


def test_pnl_bruto_de_un_corto_es_simetrico(spec: InstrumentSpec) -> None:
    assert spec.gross_pnl(Side.SELL, 2000.0, 1990.0, 0.5) == pytest.approx(500.0)
    assert spec.gross_pnl(Side.SELL, 2000.0, 2010.0, 0.5) == pytest.approx(-500.0)


def test_redondeo_a_la_rejilla_de_ticks(spec: InstrumentSpec) -> None:
    assert spec.round_price(2000.123) == pytest.approx(2000.12)
    assert spec.round_price(2000.126) == pytest.approx(2000.13)


def test_normalizar_volumen_redondea_hacia_abajo(spec: InstrumentSpec) -> None:
    # Nunca hacia arriba: redondear al alza arriesgaría más de lo pedido.
    assert spec.normalize_volume(0.1749) == pytest.approx(0.17)
    assert spec.normalize_volume(0.10) == pytest.approx(0.10)


def test_volumen_por_debajo_del_minimo_es_cero(spec: InstrumentSpec) -> None:
    assert spec.normalize_volume(0.004) == 0.0


def test_volumen_se_limita_al_maximo(spec: InstrumentSpec) -> None:
    assert spec.normalize_volume(500.0) == pytest.approx(spec.max_lot)


def test_lotaje_por_riesgo(spec: InstrumentSpec) -> None:
    # Arriesgar 100 USD con un stop de 5 USD de distancia:
    # 100 / (5 * 100 oz) = 0.20 lotes
    assert spec.lots_for_risk(risk_amount=100.0, stop_distance=5.0) == pytest.approx(0.20)


def test_lotaje_por_riesgo_exige_distancia_positiva(spec: InstrumentSpec) -> None:
    with pytest.raises(InvalidVolume):
        spec.lots_for_risk(risk_amount=100.0, stop_distance=0.0)


def test_el_riesgo_calculado_se_materializa_en_la_perdida(spec: InstrumentSpec) -> None:
    lots = spec.lots_for_risk(risk_amount=100.0, stop_distance=5.0)
    perdida = spec.gross_pnl(Side.BUY, 2000.0, 1995.0, lots)
    assert perdida == pytest.approx(-100.0)


def test_margen_requerido(spec: InstrumentSpec) -> None:
    # 1 lote a 2000 con 1:200 -> 100 * 2000 / 200 = 1000 USD
    assert spec.margin_required(1.0, 2000.0) == pytest.approx(1000.0)


def test_comision_de_ida_y_vuelta(spec: InstrumentSpec) -> None:
    assert spec.commission(1.0, sides=2) == pytest.approx(6.0)
    assert spec.commission(0.5, sides=1) == pytest.approx(1.5)


def test_swap_triple_el_dia_configurado(spec: InstrumentSpec) -> None:
    normal = spec.swap_charge(Side.BUY, 1.0, weekday=0)
    triple = spec.swap_charge(Side.BUY, 1.0, weekday=2)
    assert normal == pytest.approx(-8.0)
    assert triple == pytest.approx(-24.0)


def test_swap_corto_puede_ser_ingreso(spec: InstrumentSpec) -> None:
    assert spec.swap_charge(Side.SELL, 1.0, weekday=0) == pytest.approx(3.0)


def test_media_horquilla(spec: InstrumentSpec) -> None:
    assert spec.half_spread == pytest.approx(0.10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tick_size": 0.0},
        {"contract_size": -1.0},
        {"min_lot": 0.0},
        {"leverage": 0.0},
    ],
)
def test_especificaciones_incoherentes_fallan(kwargs: dict[str, float]) -> None:
    with pytest.raises(InvalidInstrumentSpec):
        InstrumentSpec(symbol="XAUUSD", **kwargs)


def test_costes_negativos_fallan() -> None:
    with pytest.raises(InvalidInstrumentSpec):
        CostModel(spread_points=-1.0)


def test_dia_de_swap_triple_fuera_de_rango_falla() -> None:
    with pytest.raises(InvalidInstrumentSpec):
        SwapModel(triple_swap_weekday=9)
