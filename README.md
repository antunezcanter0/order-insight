# OrderInsight

Proyecto para generar, limpiar y analizar pedidos de ejemplo.

El trabajo está dividido en fases:

1. **Generación de datos**: crea un CSV sintético reproducible con errores
   intencionados.
2. **Limpieza y persistencia** (Fase 3): valida los pedidos, elimina
   duplicados y registros inválidos, y guarda los datos válidos en SQLite.
3. **Vistas SQL** (Fase 4): crea vistas de análisis sobre la tabla `orders`.
4. **API y LLM** (Fase 5): expone un endpoint que convierte preguntas en
   lenguaje natural en SQL validado y de solo lectura.

## Requisitos

- Python 3.12.3
- Dependencias de `requirements.txt`

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
  config.py              # configuración por variables de entorno
  llm.py                 # integración con el proveedor LLM
  sql_guard.py           # validación del SQL con SQLGlot
  db.py                  # acceso SQLite de solo lectura
  service.py             # orquestación de la consulta
  api.py                 # aplicación FastAPI
data/
  orders_raw.csv         # datos originales (no se modifican)
  orders.db              # base de datos generada (no versionada)
tests/
  test_generate_orders.py
  test_clean_orders.py
  test_create_views.py
  test_sql_guard.py
  test_db.py
  test_api.py
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

## Fase 5: API en lenguaje natural con LLM

El endpoint `POST /query` recibe una pregunta, pide al LLM una consulta SQL,
la valida con SQLGlot y la ejecuta en SQLite en modo de solo lectura.

### Configuración

Copia el fichero de ejemplo y completa la clave:

```bash
cp .env.example .env
```

| Variable | Por defecto | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | — | Clave del proveedor. No se versiona. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo utilizado. |
| `OPENAI_BASE_URL` | — | Opcional, para proveedores compatibles. |
| `ORDERS_DB_PATH` | `data/orders.db` | Base de datos a consultar. |
| `MAX_RESULT_ROWS` | `200` | Máximo de filas devueltas. |
| `SQL_TIMEOUT_SECONDS` | `5` | Tiempo máximo de ejecución SQL. |
| `LLM_TIMEOUT_SECONDS` | `20` | Timeout del proveedor. |

La aplicación puede arrancar sin `OPENAI_API_KEY`; si se consulta sin clave
configurada se devuelve un error `llm_error` sin llamar a ningún servicio.

### Arranque

```bash
uvicorn app.api:app --reload
```

Documentación interactiva: `http://127.0.0.1:8000/docs`.

### Ejemplo de petición

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Qué países vendieron más en 2024?"}'
```

Respuesta:

```json
{
  "question": "¿Qué países vendieron más en 2024?",
  "sql": "SELECT country, SUM(amount) AS total_sales FROM orders WHERE strftime('%Y', order_date) = '2024' GROUP BY country ORDER BY total_sales DESC",
  "columns": ["country", "total_sales"],
  "rows": [
    {"country": "España", "total_sales": 12345.67}
  ],
  "row_count": 1,
  "truncated": false
}
```

La pregunta admite entre 1 y 1000 caracteres y no se aceptan campos
adicionales en el JSON.

### Seguridad del SQL

El SQL del LLM **nunca se considera confiable**:

- Debe ser exactamente una sentencia y su raíz debe ser `SELECT`.
- Se rechazan `INSERT`, `UPDATE`, `DELETE`, DDL, `PRAGMA`, `ATTACH`,
  `VACUUM`, `SELECT INTO` y cualquier operación administrativa.
- Se rechazan `UNION`, `INTERSECT` y `EXCEPT` como operador raíz.
- Solo se permiten `orders`, `sales_by_month_country` y `top_10_customers`.
  Las CTE, subconsultas y `JOIN` se validan atravesando todo el AST.
- Se rechazan nombres cualificados (`main.orders`) y objetos internos como
  `sqlite_master`.
- Se rechazan funciones que cargan extensiones, acceden a ficheros o
  generan bloques desproporcionados (`load_extension`, `readfile`,
  `randomblob`, etc.).
- Se ejecuta únicamente el SQL canónico generado por SQLGlot, no el texto
  original del modelo.
- La conexión usa `mode=ro` y `PRAGMA query_only=ON`.
- Se aplican timeout por `set_progress_handler` y un límite de filas; nunca
  se expone la ruta local de la base de datos en los errores.
- No se reintenta automáticamente un SQL inválido.

### Errores

| HTTP | Código | Causa |
|---|---|---|
| 422 | `invalid_request` | Pregunta vacía, demasiado larga o payload inválido. |
| 422 | `invalid_sql` | SQL no parseable, múltiples sentencias o error de ejecución. |
| 403 | `unauthorized_query` | Tabla, función u operación no autorizada. |
| 502 | `llm_error` | Fallo o falta de configuración del proveedor LLM. |
| 504 | `query_timeout` | Consulta interrumpida por timeout. |
| 503 | `database_unavailable` | Base de datos inexistente o no legible. |
| 500 | `internal_error` | Error inesperado, sin detalles internos. |

## Pruebas

```bash
pytest -v
```
