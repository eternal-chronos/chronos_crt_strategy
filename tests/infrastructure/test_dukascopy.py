"""Descarga y descodificación del histórico de Dukascopy.

El test que más importa aquí es el del relleno: Dukascopy publica los 1440
minutos de todos los días, también los que el mercado está cerrado, repitiendo
el último precio con volumen cero. Si ese relleno entra en el dataset, el hueco
de fin de semana desaparece —y con él la verificación de zona horaria de §1.1— y
el módulo se inventa velas H4 de sábado que el propietario no ve en su pantalla.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from chronos.infrastructure.data.dukascopy import (
    DukascopyClient,
    DukascopyError,
    decimals_for,
    decode_hour_candles,
    decode_minute_candles,
    month_url_for,
    url_for,
)

XAUUSD_SCALE = 10.0 ** decimals_for("XAUUSD")

#: Caché que dejó la descarga real, si esta máquina llegó a hacerla.
REAL_CACHE = Path("data/raw/dukascopy/XAUUSD/bid/2018/01")


def make_bi5(records: list[tuple[int, int, int, int, int, float]]) -> bytes:
    """Construye un `.bi5` como los que publica Dukascopy."""
    raw = b"".join(struct.pack(">5if", *record) for record in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


# --- URLs -------------------------------------------------------------------


def test_el_mes_de_la_url_va_indexado_desde_cero() -> None:
    """Enero es 00 y diciembre 11: es el error clásico con este servidor."""
    assert url_for("XAUUSD", "bid", date(2018, 1, 2)).endswith(
        "/XAUUSD/2018/00/02/BID_candles_min_1.bi5"
    )
    assert url_for("xauusd", "ask", date(2025, 12, 31)).endswith(
        "/XAUUSD/2025/11/31/ASK_candles_min_1.bi5"
    )


def test_la_ruta_de_cache_usa_el_mes_natural(tmp_path: Path) -> None:
    client = DukascopyClient(tmp_path)
    ruta = client.cache_path("XAUUSD", "bid", date(2018, 1, 2))
    assert ruta == tmp_path / "XAUUSD" / "bid" / "2018" / "01" / "02.bi5"


# --- Descodificación --------------------------------------------------------


def test_los_precios_llegan_como_enteros_escalados() -> None:
    payload = make_bi5([(0, 1306711, 1306698, 1306682, 1306809, 0.0432)])
    frame = decode_minute_candles(payload, date(2018, 1, 2), XAUUSD_SCALE)

    assert len(frame) == 1
    fila = frame.iloc[0]
    assert fila["open"] == pytest.approx(1306.711)
    assert fila["close"] == pytest.approx(1306.698)
    assert fila["low"] == pytest.approx(1306.682)
    assert fila["high"] == pytest.approx(1306.809)
    assert fila["volume"] == pytest.approx(0.0432, rel=1e-5)


def test_el_tiempo_son_segundos_desde_medianoche_utc() -> None:
    payload = make_bi5(
        [(0, 1300000, 1300100, 1299900, 1300200, 1.0), (3660, 1300100, 1300200, 1300000, 1300300, 1.0)]
    )
    frame = decode_minute_candles(payload, date(2018, 1, 2), XAUUSD_SCALE)

    assert list(frame.index) == [
        pd.Timestamp(datetime(2018, 1, 2, 0, 0, tzinfo=UTC)),
        pd.Timestamp(datetime(2018, 1, 2, 1, 1, tzinfo=UTC)),
    ]
    assert frame.index.tz is not None


def test_el_relleno_de_volumen_cero_se_descarta() -> None:
    """Los minutos sin un solo tick no son velas planas: son ausencia de dato."""
    payload = make_bi5(
        [
            (0, 1300000, 1300100, 1299900, 1300200, 1.5),  # negociado
            (60, 1300100, 1300100, 1300100, 1300100, 0.0),  # relleno
            (120, 1300100, 1300100, 1300100, 1300100, 0.0),  # relleno
            (180, 1300100, 1300300, 1300050, 1300400, 2.0),  # negociado
        ]
    )
    frame = decode_minute_candles(payload, date(2018, 1, 2), XAUUSD_SCALE)

    assert len(frame) == 2
    assert list(frame.index.minute) == [0, 3]
    assert (frame["volume"] > 0).all()


def test_un_dia_entero_de_relleno_no_produce_ninguna_barra() -> None:
    """Es lo que devuelve Dukascopy un sábado: 1440 registros y ni un tick."""
    payload = make_bi5([(minute * 60, 1300000, 1300000, 1300000, 1300000, 0.0) for minute in range(1440)])
    assert decode_minute_candles(payload, date(2018, 1, 6), XAUUSD_SCALE).empty


def test_un_dia_ausente_devuelve_una_serie_vacia() -> None:
    frame = decode_minute_candles(b"", date(2018, 1, 1), XAUUSD_SCALE)
    assert frame.empty
    assert frame.index.tz is not None  # sigue siendo tz-aware, para poder concatenar


def test_un_fichero_ilegible_se_denuncia() -> None:
    with pytest.raises(DukascopyError, match="no se pudo descomprimir"):
        decode_minute_candles(b"esto no es lzma", date(2018, 1, 2), XAUUSD_SCALE)


# --- Ficheros mensuales de velas H1 -----------------------------------------


def test_la_url_mensual_no_lleva_dia() -> None:
    assert month_url_for("XAUUSD", "bid", date(2018, 1, 1)).endswith(
        "/XAUUSD/2018/00/BID_candles_hour_1.bi5"
    )


def test_en_el_fichero_mensual_el_tiempo_cuenta_desde_el_dia_uno() -> None:
    payload = make_bi5(
        [
            (0, 1300000, 1300100, 1299900, 1300200, 1.0),
            (86400 * 5 + 3600 * 7, 1310000, 1310100, 1309900, 1310200, 1.0),
        ]
    )
    frame = decode_hour_candles(payload, date(2018, 3, 1), XAUUSD_SCALE)

    assert list(frame.index) == [
        pd.Timestamp(datetime(2018, 3, 1, 0, 0, tzinfo=UTC)),
        pd.Timestamp(datetime(2018, 3, 6, 7, 0, tzinfo=UTC)),
    ]


def test_una_marca_de_tiempo_fuera_del_mes_delata_otro_formato() -> None:
    """Guardarraíl barato contra el fallo silencioso de leer mal el campo."""
    payload = make_bi5([(86400 * 40, 1300000, 1300100, 1299900, 1300200, 1.0)])
    with pytest.raises(DukascopyError, match="fuera del mes"):
        decode_hour_candles(payload, date(2018, 2, 1), XAUUSD_SCALE)


def test_la_descarga_mensual_recorta_a_las_fechas_pedidas(tmp_path: Path) -> None:
    """Los ficheros vienen por meses completos y el periodo casi nunca lo es."""
    payload = make_bi5(
        [(hour * 3600, 1300000, 1300100, 1299900, 1300200, 1.0) for hour in range(24 * 28)]
    )
    client = _FakeClient(tmp_path, [(200, payload)])
    frame = client.download_hourly("XAUUSD", date(2018, 1, 10), date(2018, 1, 12), "bid")

    assert frame.index[0] >= pd.Timestamp(datetime(2018, 1, 10, tzinfo=UTC))
    assert frame.index[-1] <= pd.Timestamp(datetime(2018, 1, 12, 23, 59, 59, tzinfo=UTC))
    assert len(frame) == 24 * 3


def test_la_descarga_mensual_pide_un_fichero_por_mes(tmp_path: Path) -> None:
    payload = make_bi5([(0, 1300000, 1300100, 1299900, 1300200, 1.0)])
    client = _FakeClient(tmp_path, [(200, payload)])
    client.download_hourly("XAUUSD", date(2018, 1, 15), date(2018, 4, 2), "bid")
    assert client.calls == 4  # enero, febrero, marzo y abril


def test_la_cache_mensual_no_pisa_a_la_diaria(tmp_path: Path) -> None:
    client = DukascopyClient(tmp_path)
    diaria = client.cache_path("XAUUSD", "bid", date(2018, 1, 2))
    mensual = client.month_cache_path("XAUUSD", "bid", date(2018, 1, 1))
    assert diaria != mensual
    assert "h1" in mensual.parts


# --- Cliente: caché, ausencias y errores ------------------------------------


class _FakeClient(DukascopyClient):
    """Cliente sin red: responde lo que le digan y cuenta las peticiones."""

    def __init__(self, cache_dir: Path, responses: list[tuple[int, bytes]]) -> None:
        super().__init__(cache_dir)
        self.responses = responses
        self.calls = 0

    def _request(self, url: str) -> tuple[int, bytes]:
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def test_lo_descargado_no_se_vuelve_a_pedir(tmp_path: Path) -> None:
    """Una descarga de ocho años se interrumpe seguro: tiene que ser reanudable."""
    payload = make_bi5([(0, 1300000, 1300100, 1299900, 1300200, 1.0)])
    client = _FakeClient(tmp_path, [(200, payload)])

    primera = client.fetch("XAUUSD", "bid", date(2018, 1, 2))
    segunda = client.fetch("XAUUSD", "bid", date(2018, 1, 2))

    assert primera == segunda == payload
    assert client.calls == 1


def test_un_dia_que_el_servidor_no_tiene_es_una_ausencia_no_un_error(tmp_path: Path) -> None:
    client = _FakeClient(tmp_path, [(404, b"")])
    assert client.fetch("XAUUSD", "bid", date(2018, 1, 1)) == b""
    # La ausencia también se cachea: no tiene sentido volver a preguntarla.
    client.fetch("XAUUSD", "bid", date(2018, 1, 1))
    assert client.calls == 1


def test_el_limite_de_ritmo_se_reintenta_y_acaba_saliendo(tmp_path: Path) -> None:
    payload = make_bi5([(0, 1300000, 1300100, 1299900, 1300200, 1.0)])
    client = _FakeClient(tmp_path, [(503, b""), (503, b""), (200, payload)])
    client._guard._floor = client._guard._interval = 0.0  # sin esperas en el test

    assert client.fetch("XAUUSD", "bid", date(2018, 1, 2)) == payload
    assert client.calls == 3


def test_si_el_limite_no_cede_la_descarga_falla_con_un_mensaje_claro(tmp_path: Path) -> None:
    client = _FakeClient(tmp_path, [(503, b"")])
    client._guard._floor = client._guard._interval = 0.0
    client._max_retries = 3

    with pytest.raises(DukascopyError, match="HTTP 503"):
        client.fetch("XAUUSD", "bid", date(2018, 1, 2))
    assert client.calls == 3


def test_una_respuesta_inesperada_no_se_reintenta_en_silencio(tmp_path: Path) -> None:
    client = _FakeClient(tmp_path, [(403, b"")])
    with pytest.raises(DukascopyError, match="HTTP 403"):
        client.fetch("XAUUSD", "bid", date(2018, 1, 2))
    assert client.calls == 1


def test_las_fechas_al_reves_se_rechazan(tmp_path: Path) -> None:
    client = _FakeClient(tmp_path, [(200, b"")])
    with pytest.raises(DukascopyError, match="posterior"):
        client.download("XAUUSD", date(2018, 2, 1), date(2018, 1, 1), "bid")


def test_un_lado_inexistente_se_rechaza(tmp_path: Path) -> None:
    client = _FakeClient(tmp_path, [(200, b"")])
    with pytest.raises(DukascopyError, match="Lado desconocido"):
        client.download("XAUUSD", date(2018, 1, 1), date(2018, 1, 2), "medio")


def test_la_serie_descargada_va_ordenada_y_sin_duplicados(tmp_path: Path) -> None:
    payload = make_bi5(
        [(0, 1300000, 1300100, 1299900, 1300200, 1.0), (60, 1300100, 1300200, 1300000, 1300300, 1.0)]
    )
    client = _FakeClient(tmp_path, [(200, payload)])
    frame = client.download("XAUUSD", date(2018, 1, 2), date(2018, 1, 4), "bid")

    assert len(frame) == 6  # tres días de dos minutos
    assert frame.index.is_monotonic_increasing
    assert not frame.index.has_duplicates
    assert frame.index.name == "timestamp"


# --- Con los bytes reales de Dukascopy, si están en la caché ----------------


@pytest.mark.skipif(not REAL_CACHE.is_dir(), reason="no hay caché real de Dukascopy")
def test_el_sabado_real_de_dukascopy_no_tiene_ni_una_barra() -> None:
    sabado = REAL_CACHE / "06.bi5"  # 2018-01-06
    if not sabado.is_file():
        pytest.skip("el sábado no está en la caché")
    assert decode_minute_candles(sabado.read_bytes(), date(2018, 1, 6), XAUUSD_SCALE).empty


@pytest.mark.skipif(not REAL_CACHE.is_dir(), reason="no hay caché real de Dukascopy")
def test_el_viernes_real_cierra_antes_de_las_22_utc() -> None:
    viernes = REAL_CACHE / "05.bi5"  # 2018-01-05
    if not viernes.is_file():
        pytest.skip("el viernes no está en la caché")
    frame = decode_minute_candles(viernes.read_bytes(), date(2018, 1, 5), XAUUSD_SCALE)

    assert not frame.empty
    assert frame.index[-1].hour == 21
    # Enero de 2018: el oro rondaba los 1.320 USD.
    assert 1_200 < float(frame["close"].iloc[-1]) < 1_400
