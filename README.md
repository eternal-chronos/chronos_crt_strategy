# chronos-crt-strategy

Laboratorio de trading algorítmico sobre **XAUUSD** (el único par de la estrategia).

**Todavía no hay estrategia, y es a propósito.** Lo que hay es el chasis, con
todo lo que hace falta para escribir una encima:

- un **explorador HTML** autocontenido con el oro dentro, sus
  temporalidades, replay paso a paso, zoom de trading y herramientas para marcar
  a mano encima del precio;
- la **carga y agregación del histórico** M1 → M15 / H1 / H4 / Diario, con la
  rejilla de cTrader y el horario de verano resuelto de verdad;
- el **motor de backtest** con su contrato de estrategia, su bróker simulado —con
  spread, comisión, swap y slippage—, dimensionamiento de riesgo, métricas y
  panel HTML;
- la **verificación de zona horaria**, obligatoria antes de mirar nada.

Estado: **solo backtest**. El camino previsto es backtest → demo → live, y cada
salto exige trabajo explícito. La configuración tiene una puerta que rechaza
cualquier `mode` distinto de `backtest`.

## Instalación

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
source .venv/bin/activate
```

## El explorador

Es la herramienta principal mientras no haya estrategia: se abre con doble clic,
funciona sin conexión y lleva el histórico del oro dentro del fichero.

```bash
# 1. Traer el histórico. Tiene que ser M1: la verificación horaria mide el rango
#    medio POR MINUTO y con velas de una hora no distingue 13:30 de 13:00.
#    OJO: son varios GB y horas de descarga. Se reanuda si se corta.
chronos data dukascopy -g m1 -s XAUUSD \
  --from 2018-01-01 --to 2025-12-31 --sides bid

# 2. Verificar la zona horaria. Obligatorio antes de mirar nada.
chronos chart verify-tz --config config/explorer.yaml

# 3. Qué hay cargado: pares, temporalidades, velas y tramo de cada una.
chronos chart info --config config/explorer.yaml

# 4. Escribir el explorador -> now/explorador/explorador.html
chronos chart explorer --config config/explorer.yaml
```

Los pares que **no** tengan histórico descargado no rompen nada: se avisa de
cuáles faltan, se dibujan los que hay y el propio HTML lo dice en su estado. Para
mirar sólo uno: `chronos chart explorer -s XAUUSD`.

### Qué se puede hacer dentro

| Control | Qué hace | Atajo |
|---|---|---|
| **Par** | Cambia de histórico sin abrir otro fichero | `↑` `↓` |
| **Temporalidad** | Diario, H4, H1, M15 | `d` `4` `1` `m` |
| **Vista** | Velas o línea de cierres | |
| **Periodo** | Presets de 1 semana a todo, y `◀ ▶` para ir tramo a tramo | `←` `→` |
| **Replay** | Reproduce la historia paso a paso, con la vela en formación | espacio |
| **Simular entrada** | Planta una caja largo/corto y la arrastra; el R:R se **mide**, no se elige | |
| **Recuadros a mano** | Rectángulos punteados para señalar zonas | |
| **Líneas a mano** | Segmentos para señalar niveles; nacen horizontales y se inclinan | |
| **Cuenta simulada** | Apunta cada caja como ganada / perdida / BE y lleva la curva | |
| **Auditoría ciega** | Ventana al azar con semilla, para marcar antes de mirar nada | |

Además: rueda para zoom, arrastrar sobre el **eje de precios** comprime o estira
la vertical y sobre el **eje de fechas** abre o cierra el gráfico de lado, como en
cualquier plataforma. «Ajustar» o el doble clic sueltan el encuadre.

Cada par guarda **sus** marcas: lo que dibujas en el oro vuelve cuando regresas a
él, y no aparece encima del euro. La cuenta simulada, en cambio, es **una** para
los cuatro, y cada operación apuntada dice sobre qué par se dibujó.

Los nombres de los recuadros y las líneas salen de `config/explorer.yaml`
(`marks:`): cámbialos cuando la estrategia tenga vocabulario propio, sin tocar el
JavaScript.

## El backtest

```bash
# Datos sintéticos para comprobar que el pipeline funciona de punta a punta
chronos data synth --periods 200000
chronos backtest --config config/backtest.synthetic.yaml

# Sobre el histórico real
chronos backtest --config config/backtest.yaml
chronos backtest --start 2024-01-01 --end 2024-06-30

# Otros
chronos data info data/processed/XAUUSD_M1_bid.parquet
chronos strategy list
```

`ema_cross` es la estrategia **de referencia** que viene con el motor, no la del
proyecto: existe para poder correr el pipeline entero antes de que haya una
propia. Cuando sobre, se borra `src/chronos/domain/strategies/ema_cross.py` y su
sección en los YAML de backtest.

## Escribir la estrategia

1. Un fichero nuevo en `src/chronos/domain/strategies/`, decorado con
   `@register("nombre")` y heredando de `Strategy` o de `IndicatorStrategy`.
   Funciones puras sobre arrays: sin red, sin ficheros, sin reloj.
2. Sus parámetros, en un dataclass congelado, y su configuración en el YAML.
3. Sus tests en `tests/domain/`, sin mocks, con los casos límite y el test de
   no-look-ahead.
4. **Y su dibujo.** Lo que la estrategia calcule tiene que verse en el explorador
   antes de darse por terminado: capa propia, entrada en la leyenda y texto de
   estado que diga qué se está viendo y qué no. Se añade al payload en
   `infrastructure/reporting/explorer.py`, se dibuja en `assets/explorer.js` y se
   prueba con el stub del DOM.

Hay un test —`test_no_se_dibuja_ni_una_traza_que_no_sea_precio`— que hoy exige
que el explorador no pinte nada calculado. Al añadir la primera capa hay que
actualizarlo a propósito; no borrarlo.

## Estructura

```
config/
  explorer.yaml            # el oro, la rejilla y las marcas a mano
  backtest.yaml            # una corrida del motor
  instruments/xauusd.yaml  # ficha del oro: ticks, costes, swap, sesión
data/
  raw/dukascopy/           # .bi5 crudos: caché de descarga, reanudable
  processed/               # parquet canónico por par y lado
now/explorador/            # el HTML generado. Salida, no fuente
src/chronos/
  domain/                  # barras, instrumento, posición, contrato de estrategia
  domain/strategies/       # aquí va la estrategia nueva
  application/chart/       # configuración del explorador y verificación horaria
  application/backtest/    # motor, sesión, resultado
  infrastructure/market/   # carga del histórico y agregación de temporalidades
  infrastructure/reporting/# explorador, panel e informes  (assets/ = la fuente)
  interface/               # CLI: punto de composición
tests/
```

## El par: lo que no se puede olvidar

- El **pip del oro es la segunda cifra decimal** y sale de la configuración.
  Nada de decimales fijos en el código.
- Nada de umbrales en unidades de precio absolutas: ATR, pips o fracción de un
  rango.
- La ficha de instrumento trae valores de partida que **hay que verificar**
  contra la ficha real de tu cuenta en cTrader: spread, comisión, swap y
  apalancamiento cambian por entidad y tipo de cuenta.

## Comprobaciones

```bash
make check      # lint + tipos + tests
make test
make explorer   # regenera now/explorador/explorador.html
```
