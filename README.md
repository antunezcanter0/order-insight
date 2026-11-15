# OrderInsight

Proyecto para generar, limpiar y analizar pedidos de ejemplo.

El trabajo está dividido en fases:

1. **Generación de datos**: crea un CSV sintético reproducible con errores
   intencionados.
2. **Limpieza y persistencia** (Fase 3): valida los pedidos, elimina
   duplicados y registros inválidos, y guarda los datos válidos en SQLite.
3. **Vistas SQL** (Fase 4): crea vistas de análisis sobre la tabla `orders`.

La API con FastAPI y la integración con el LLM se abordarán en fases
posteriores.

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
  create_views.py        # vistas SQL de análisis
data/
  orders_raw.csv         # datos originales (no se modifican)
  orders.db              # base de datos generada (no versionada)
tests/
  test_generate_orders.py
  test_clean_orders.py
  test_create_views.py
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

## Fase 4: vistas SQL de análisis

Crea dos vistas sobre la tabla `orders` de `data/orders.db`:

```bash
python -m app.create_views
```

También se puede indicar otra base de datos:

```bash
python -m app.create_views --db data/orders.db
```

Las vistas se eliminan y recrean dentro de una transacción, por lo que el
comando puede ejecutarse tantas veces como se quiera sin errores y siempre
deja la definición actual. No modifican la tabla `orders` ni crean tablas
adicionales. Las vistas se conservan aunque después se ejecute de nuevo
`python -m app.clean_orders`, que reconstruye `orders` con los mismos
campos.

Ambas vistas incluyen **todos los estados** de pedido (`delivered`,
`shipped`, `pending`, `cancelled` y `returned`): no se aplica ningún filtro
por estado, ya que el análisis contempla el conjunto completo de pedidos.

### `sales_by_month_country`

Agrupa los pedidos por año, mes y país:

| Columna | Descripción |
|---|---|
| `year` | Año del pedido (entero). |
| `month` | Mes del pedido (entero, 1-12). |
| `country` | País del pedido. |
| `total_orders` | Cantidad de pedidos del grupo. |
| `total_sales` | Suma de los importes del grupo. |
| `average_order_value` | Importe medio por pedido del grupo. |

```sql
SELECT *
FROM sales_by_month_country
ORDER BY year, month, country;
```

### `top_10_customers`

Devuelve los diez clientes con mayor volumen de ventas:

| Columna | Descripción |
|---|---|
| `customer` | Nombre del cliente. |
| `total_orders` | Cantidad de pedidos del cliente. |
| `total_sales` | Suma de los importes de sus pedidos. |
| `average_order_value` | Importe medio por pedido del cliente. |

```sql
SELECT *
FROM top_10_customers
ORDER BY total_sales DESC, customer ASC;
```

La vista ya aplica `ORDER BY total_sales DESC, customer ASC` y `LIMIT 10`;
el `ORDER BY` del ejemplo hace explícito el orden de presentación.

## Pruebas

```bash
pytest -v
```
