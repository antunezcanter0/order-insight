"""Vistas SQL de análisis sobre la tabla ``orders``.

Crea (o actualiza) de forma idempotente las vistas ``sales_by_month_country``
y ``top_10_customers`` en la base de datos SQLite generada por
``app.clean_orders``. No modifica la tabla ``orders`` ni crea tablas
adicionales.

Uso:
    python -m app.create_views
    python -m app.create_views --db data/orders.db

Solo utiliza la biblioteca estándar de Python.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path("data/orders.db")

SALES_BY_MONTH_COUNTRY = """
CREATE VIEW sales_by_month_country AS
SELECT
    CAST(strftime('%Y', order_date) AS INTEGER) AS year,
    CAST(strftime('%m', order_date) AS INTEGER) AS month,
    country,
    COUNT(*)    AS total_orders,
    SUM(amount) AS total_sales,
    AVG(amount) AS average_order_value
FROM orders
GROUP BY year, month, country
ORDER BY year, month, country
"""

TOP_10_CUSTOMERS = """
CREATE VIEW top_10_customers AS
SELECT
    customer,
    COUNT(*)    AS total_orders,
    SUM(amount) AS total_sales,
    AVG(amount) AS average_order_value
FROM orders
GROUP BY customer
ORDER BY total_sales DESC, customer ASC
LIMIT 10
"""

# Nombre -> sentencia CREATE VIEW.
VIEW_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("sales_by_month_country", SALES_BY_MONTH_COUNTRY),
    ("top_10_customers", TOP_10_CUSTOMERS),
)

VIEW_NAMES = tuple(name for name, _ in VIEW_DEFINITIONS)


class ViewsError(Exception):
    """Error de entrada que impide crear las vistas."""


def _orders_table_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'orders'
        """
    ).fetchone()
    return row is not None


def create_views(db_path: Path | str = DEFAULT_DB) -> list[str]:
    """Crea o recrea las vistas de análisis de forma atómica.

    Se eliminan las vistas previas y se vuelven a crear dentro de una
    única transacción: si algo falla, el ``ROLLBACK`` restaura las vistas
    anteriores. La tabla ``orders`` no se modifica.
    """
    destination = Path(db_path)
    if not destination.exists():
        raise ViewsError(f"no existe la base de datos: {destination}")

    connection = sqlite3.connect(destination, isolation_level=None)
    try:
        if not _orders_table_exists(connection):
            raise ViewsError(
                f"la base de datos no contiene la tabla orders: {destination}"
            )

        connection.execute("BEGIN IMMEDIATE")
        try:
            for name in VIEW_NAMES:
                connection.execute(f"DROP VIEW IF EXISTS {name}")
            for _, statement in VIEW_DEFINITIONS:
                connection.execute(statement)
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
    finally:
        connection.close()

    return list(VIEW_NAMES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crea las vistas SQL de análisis sobre la tabla orders "
            "en SQLite."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"base de datos SQLite (por defecto: {DEFAULT_DB})",
    )
    return parser


def print_summary(db_path: Path, view_names: list[str]) -> None:
    print("Vistas creadas:")
    for name in view_names:
        print(f"  - {name}")
    print(f"Base de datos: {db_path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        view_names = create_views(db_path=args.db)
    except (ViewsError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print_summary(args.db, view_names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
