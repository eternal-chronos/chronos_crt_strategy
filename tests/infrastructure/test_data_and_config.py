"""Datos, calendario y configuración."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chronos.application.backtest.session import build_bar_flags
from chronos.domain.bars import (
    normalize_bars,
    resample_bars,
    validate_bars,
)
from chronos.domain.enums import Timeframe
from chronos.domain.errors import InvalidPrice
from chronos.domain.instrument import InstrumentSpec, SessionSpec, SwapModel
from chronos.infrastructure.config.loader import ConfigError, load_backtest_config, load_instrument
from chronos.infrastructure.data.synthetic import generate_ohlcv
from tests.conftest import make_frame

# --- Normalización ----------------------------------------------------------


def test_normalizar_acepta_nombres_de_columna_del_broker() -> None:
    raw = pd.DataFrame(
        {
            "Gmt time": ["2024-03-04 10:00", "2024-03-04 10:01"],
            "Open": [2000.0, 2001.0],
            "High": [2002.0, 2003.0],
            "Low": [1999.0, 2000.0],
            "Close": [2001.0, 2002.0],
            "Volume": [100, 120],
        }
    )
    frame = normalize_bars(raw)

    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert str(frame.index.tz) == "UTC"
    validate_bars(frame)


def test_las_marcas_naif_se_interpretan_en_la_zona_indicada() -> None:
    raw = pd.DataFrame(
        {
            "timestamp": ["2024-03-04 10:00"],
            "open": [2000.0], "high": [2001.0], "low": [1999.0], "close": [2000.5],
        }
    )
    frame = normalize_bars(raw, timezone="Europe/Athens")
    assert frame.index[0] == pd.Timestamp("2024-03-04 08:00", tz="UTC")


def test_se_descartan_marcas_duplicadas() -> None:
    raw = pd.DataFrame(
        {
            "timestamp": ["2024-03-04 10:00", "2024-03-04 10:00"],
            "open": [2000.0, 2010.0], "high": [2001.0, 2011.0],
            "low": [1999.0, 2009.0], "close": [2000.5, 2010.5],
        }
    )
    frame = normalize_bars(raw)
    assert len(frame) == 1
    assert frame["open"].iloc[0] == pytest.approx(2010.0)  # se conserva la última


def test_faltan_columnas_obligatorias() -> None:
    with pytest.raises(InvalidPrice):
        normalize_bars(pd.DataFrame({"timestamp": ["2024-03-04"], "open": [2000.0]}))


def test_barras_incoherentes_se_detectan() -> None:
    frame = make_frame([(2000.0, 1999.0, 2001.0, 2000.0)])  # high < low
    with pytest.raises(InvalidPrice):
        validate_bars(frame)


# --- Resampleo --------------------------------------------------------------


def test_el_resampleo_conserva_el_recorrido() -> None:
    frame = make_frame(
        [
            (2000.0, 2005.0, 1998.0, 2003.0),
            (2003.0, 2010.0, 2002.0, 2008.0),
            (2008.0, 2009.0, 1995.0, 1996.0),
            (1996.0, 1997.0, 1990.0, 1992.0),
        ]
    )
    hourly = resample_bars(frame, Timeframe.H1)

    assert len(hourly) == 1
    assert hourly["open"].iloc[0] == pytest.approx(2000.0)
    assert hourly["high"].iloc[0] == pytest.approx(2010.0)
    assert hourly["low"].iloc[0] == pytest.approx(1990.0)
    assert hourly["close"].iloc[0] == pytest.approx(1992.0)


def test_la_barra_se_etiqueta_con_el_inicio_del_intervalo() -> None:
    frame = make_frame([(2000.0, 2001.0, 1999.0, 2000.0)] * 4, start="2024-03-04 10:00")
    hourly = resample_bars(frame, Timeframe.H1)
    assert hourly.index[0] == pd.Timestamp("2024-03-04 10:00", tz="UTC")


# --- Calendario -------------------------------------------------------------


def test_el_swap_se_devenga_una_vez_al_cruzar_el_rollover() -> None:
    spec = InstrumentSpec(
        symbol="XAUUSD",
        swap=SwapModel(rollover_hour_server=0),
        session=SessionSpec(timezone="UTC", break_start=None, break_end=None),
    )
    frame = make_frame([(2000.0, 2001.0, 1999.0, 2000.0)] * 100, start="2024-03-04 22:00")
    flags = build_bar_flags(pd.DatetimeIndex(frame.index), spec)

    # 100 barras de 15 min = 25 horas: exactamente un cruce de medianoche.
    assert int(flags.is_rollover.sum()) == 1
    assert not flags.is_rollover[0]  # la primera barra no devenga swap


def test_el_corte_diario_marca_la_sesion_como_cerrada() -> None:
    from datetime import time

    spec = InstrumentSpec(
        symbol="XAUUSD",
        session=SessionSpec(timezone="UTC", break_start=time(0, 0), break_end=time(1, 0)),
    )
    frame = make_frame([(2000.0, 2001.0, 1999.0, 2000.0)] * 8, start="2024-03-04 23:00")
    flags = build_bar_flags(pd.DatetimeIndex(frame.index), spec)

    # 4 barras antes de medianoche abiertas, 4 dentro del corte cerradas.
    assert flags.session_open[:4].all()
    assert not flags.session_open[4:].any()


# --- Datos sintéticos -------------------------------------------------------


def test_los_datos_sinteticos_cumplen_las_invariantes_ohlc() -> None:
    frame = generate_ohlcv(periods=5_000, seed=7, spread_points=20)
    validate_bars(frame)
    assert "spread" in frame.columns
    assert (frame["spread"] > 0).all()


def test_la_semilla_hace_reproducible_la_serie() -> None:
    a = generate_ohlcv(periods=500, seed=11)
    b = generate_ohlcv(periods=500, seed=11)
    pd.testing.assert_frame_equal(a, b)


# --- Configuración ----------------------------------------------------------


def test_se_carga_la_ficha_de_xauusd() -> None:
    spec = load_instrument("xauusd", Path("config/instruments"))

    assert spec.symbol == "XAUUSD"
    assert spec.contract_size == pytest.approx(100.0)
    assert spec.value_per_point_per_lot == pytest.approx(1.0)


def test_se_carga_la_configuracion_de_backtest() -> None:
    config = load_backtest_config(Path("config/backtest.yaml"))

    assert config.execution.fill_model == "next_bar_open"
    assert config.data.strategy_timeframe is Timeframe.M15
    assert config.risk.max_concurrent_positions >= 1


def test_los_modos_demo_y_live_estan_cerrados(tmp_path: Path) -> None:
    """Puerta de seguridad: no se puede pasar a live editando un YAML."""
    path = tmp_path / "live.yaml"
    path.write_text("mode: live\nstrategy:\n  name: ema_cross\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_backtest_config(path)


def test_una_clave_desconocida_no_pasa_en_silencio(tmp_path: Path) -> None:
    path = tmp_path / "typo.yaml"
    path.write_text("mode: backtest\nstrategy:\n  name: x\nriskk: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_backtest_config(path)
