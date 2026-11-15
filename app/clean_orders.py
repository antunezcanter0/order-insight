"""Limpieza de pedidos y persistencia en SQLite.

Lee ``data/orders_raw.csv``, valida cada pedido, descarta los registros
inválidos y los duplicados, y reconstruye de forma atómica la tabla
``orders`` de una base de datos SQLite con los pedidos válidos.

Cada ejecución reemplaza el contenido anterior de la tabla: la base de
datos siempre refleja el CSV procesado en ese momento.

Uso:
    python -m app.clean_orders
    python -m app.clean_orders --input data/orders_raw.csv --db data/orders.db

Solo utiliza la biblioteca estándar de Python.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

DEFAULT_INPUT = Path("data/orders_raw.csv")
DEFAULT_DB = Path("data/orders.db")

FIELDNAMES = [
    "order_id",
    "order_date",
    "customer",
    "country",
    "product",
    "amount",
    "status",
]

# Campos de texto que deben estar presentes y no vacíos.
TEXT_FIELDS = ("order_id", "customer", "country", "product", "status")

# Validación estricta: solo se acepta el formato ISO extendido YYYY-MM-DD.
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_SCHEMA = """
CREATE TABLE orders (
    order_id   TEXT PRIMARY KEY,
    order_date TEXT NOT NULL,
    customer   TEXT NOT NULL,
    country    TEXT NOT NULL,
    product    TEXT NOT NULL,
    amount     REAL NOT NULL CHECK (amount >= 0),
    status     TEXT NOT NULL
)
"""


class CleanOrdersError(Exception):
    """Error de entrada que impide iniciar el proceso de limpieza."""


@dataclass(frozen=True)
class Order:
    """Pedido validado y listo para persistirse."""

    order_id: str
    order_date: date
    customer: str
    country: str
    product: str
    amount: Decimal
    status: str


@dataclass(frozen=True)
class CleaningReport:
    """Resumen del proceso de limpieza."""

    input_path: Path
    db_path: Path
    total_rows: int
    duplicates: int
    invalid_dates: int
    invalid_amounts: int
    invalid_fields: int
    stored: int

    @property
    def discarded(self) -> int:
        return (
            self.duplicates
            + self.invalid_dates
            + self.invalid_amounts
            + self.invalid_fields
        )


def _parse_date(value: str) -> date:
    """Convierte una fecha ISO estricta o lanza ``ValueError``."""
    if not _ISO_DATE_RE.fullmatch(value):
        raise ValueError(f"fecha no ISO: {value!r}")
    return date.fromisoformat(value)


def _parse_amount(value: str) -> Decimal:
    """Convierte un importe numérico finito y no negativo."""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"importe no numérico: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"importe no finito: {value!r}")
    if amount < 0:
        raise ValueError(f"importe negativo: {value!r}")
    return amount


def _parse_row(row: dict[str, str | None]) -> tuple[Order | None, str | None]:
    """Valida una fila.

    Devuelve el pedido o el motivo de rechazo. El orden de comprobación es
    campos obligatorios, fecha, importe; así cada fila descartada cae en
    una única categoría.
    """
    values = {
        field: (row.get(field) or "").strip()
        for field in FIELDNAMES
    }

    if any(not values[field] for field in TEXT_FIELDS):
        return None, "invalid_fields"

    try:
        order_date = _parse_date(values["order_date"])
    except ValueError:
        return None, "invalid_dates"

    try:
        amount = _parse_amount(values["amount"])
    except ValueError:
        return None, "invalid_amounts"

    return (
        Order(
            order_id=values["order_id"],
            order_date=order_date,
            customer=values["customer"],
            country=values["country"],
            product=values["product"],
            amount=amount,
            status=values["status"],
        ),
        None,
    )


def _read_rows(input_path: Path) -> list[dict[str, str | None]]:
    with input_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [field for field in FIELDNAMES if field not in fieldnames]
        if missing:
            raise CleanOrdersError(
                "faltan columnas obligatorias en el CSV: "
                + ", ".join(missing)
            )
        return list(reader)


def _validate_and_deduplicate(
    rows: list[dict[str, str | None]],
) -> tuple[list[Order], dict[str, int]]:
    """Valida las filas y conserva la primera aparición válida de cada id.

    Los registros inválidos se descartan antes de deduplicar, de modo que
    una fila válida no se pierde porque otra aparición del mismo
    ``order_id`` sea inválida.
    """
    counts = {
        "duplicates": 0,
        "invalid_dates": 0,
        "invalid_amounts": 0,
        "invalid_fields": 0,
    }
    orders: list[Order] = []
    seen_ids: set[str] = set()

    for row in rows:
        order, reason = _parse_row(row)
        if reason is not None:
            counts[reason] += 1
            continue

        assert order is not None
        if order.order_id in seen_ids:
            counts["duplicates"] += 1
            continue

        seen_ids.add(order.order_id)
        orders.append(order)

    return orders, counts


def _insert_orders(connection: sqlite3.Connection, orders: list[Order]) -> None:
    connection.executemany(
        """
        INSERT INTO orders (
            order_id, order_date, customer, country, product, amount, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                order.order_id,
                order.order_date.isoformat(),
                order.customer,
                order.country,
                order.product,
                float(order.amount),
                order.status,
            )
            for order in orders
        ],
    )


def _replace_orders(db_path: Path, orders: list[Order]) -> None:
    """Reconstruye la tabla ``orders`` dentro de una transacción atómica.

    El ``BEGIN`` explícito incluye también el ``DROP`` y el ``CREATE`` en
    la transacción: si algo falla, el ``ROLLBACK`` conserva la tabla y los
    datos anteriores.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DROP TABLE IF EXISTS orders")
            connection.execute(_SCHEMA)
            _insert_orders(connection, orders)
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
    finally:
        connection.close()


def clean_orders(
    input_path: Path | str = DEFAULT_INPUT,
    db_path: Path | str = DEFAULT_DB,
) -> CleaningReport:
    """Limpia el CSV y reconstruye la tabla ``orders`` de la base SQLite."""
    source = Path(input_path)
    destination = Path(db_path)

    rows = _read_rows(source)
    orders, counts = _validate_and_deduplicate(rows)
    _replace_orders(destination, orders)

    return CleaningReport(
        input_path=source,
        db_path=destination,
        total_rows=len(rows),
        duplicates=counts["duplicates"],
        invalid_dates=counts["invalid_dates"],
        invalid_amounts=counts["invalid_amounts"],
        invalid_fields=counts["invalid_fields"],
        stored=len(orders),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Limpia el CSV de pedidos y reconstruye la tabla orders "
            "en SQLite."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"CSV de entrada (por defecto: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"base de datos SQLite de salida (por defecto: {DEFAULT_DB})",
    )
    return parser


def print_summary(report: CleaningReport) -> None:
    print(f"Registros leídos:            {report.total_rows}")
    print(f"Duplicados descartados:      {report.duplicates}")
    print(f"Fechas inválidas:            {report.invalid_dates}")
    print(f"Importes inválidos:          {report.invalid_amounts}")
    print(f"Campos obligatorios vacíos:  {report.invalid_fields}")
    print(f"Registros válidos guardados: {report.stored}")
    print(f"Base de datos:               {report.db_path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = clean_orders(input_path=args.input, db_path=args.db)
    except (CleanOrdersError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
