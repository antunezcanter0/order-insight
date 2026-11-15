# OrderInsight

Proyecto para generar, limpiar y analizar pedidos de ejemplo.

El trabajo está dividido en fases:

1. **Generación de datos**: crea un CSV sintético reproducible con errores
   intencionados.
2. **Limpieza y persistencia** (Fase 3): valida los pedidos, elimina
   duplicados y registros inválidos, y guarda los datos válidos en SQLite.

Las vistas SQL, la API con FastAPI y la integración con el LLM se abordarán
en fases posteriores.

## Requisitos

- Python 3.12.3
- Dependencias de `requirements.txt` (para esta fase solo se usa la
  biblioteca estándar)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Estructura

```text
app/
  generate_orders.py     # generador del CSV de ejemplo
  clean_orders.py        # limpieza y persistencia en SQLite
data/
  orders_raw.csv         # datos originales (no se modifican)
  orders.db              # base de datos generada (no versionada)
tests/
  test_generate_orders.py
  test_clean_orders.py
```

## Fase 1: generar los pedidos

```bash
python -m app.generate_orders
```

Opciones disponibles:

```bash
python -m app.generate_orders --rows 2000 --seed 7
```

El CSV se escribe en `data/orders_raw.csv` e incluye de forma deliberada
identificadores duplicados, fechas no ISO e importes ausentes.

## Fase 3: limpieza y persistencia en SQLite

El proceso lee `data/orders_raw.csv` **sin modificarlo**, valida cada pedido
y reconstruye de forma atómica la tabla `orders` de `data/orders.db`. Cada
ejecución reemplaza el contenido anterior de la tabla con los registros
válidos del CSV actual.

```bash
python -m app.clean_orders
```

También se pueden indicar otras rutas:

```bash
python -m app.clean_orders --input data/orders_raw.csv --db data/orders.db
```

### Reglas de limpieza

- Todos los campos obligatorios deben estar presentes y no vacíos.
- La fecha debe tener el formato ISO estricto `YYYY-MM-DD` y ser una fecha
  existente. Formatos alternativos como `2025/12/15`, `July 23, 2025` o
  `24/03/2025` se rechazan.
- El importe debe ser numérico, finito y no negativo.
- Los registros con algún error se excluyen y se contabilizan en el resumen.
- Ante varios pedidos con el mismo `order_id` se conserva la **primera
  aparición válida** y el resto se cuentan como duplicados.
- Los registros inválidos se descartan antes de deduplicar, de modo que un
  pedido válido no se pierde si otra aparición del mismo `order_id` es
  inválida.

La tabla `orders` se crea con este esquema:

```sql
CREATE TABLE orders (
    order_id   TEXT PRIMARY KEY,
    order_date TEXT NOT NULL,
    customer   TEXT NOT NULL,
    country    TEXT NOT NULL,
    product    TEXT NOT NULL,
    amount     REAL NOT NULL CHECK (amount >= 0),
    status     TEXT NOT NULL
);
```

El `DROP TABLE`, el `CREATE TABLE` y la inserción se ejecutan dentro de una
única transacción: si algo falla, se hace `ROLLBACK` y se conservan la tabla
y los datos anteriores.

### Ejemplo de resumen

Salida con el CSV incluido en el repositorio:

```text
Registros leídos:            1000
Duplicados descartados:      20
Fechas inválidas:            20
Importes inválidos:          20
Campos obligatorios vacíos:  0
Registros válidos guardados: 940
Base de datos:               data/orders.db
```

## Pruebas

```bash
pytest -v
```
