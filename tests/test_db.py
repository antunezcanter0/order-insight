"""Pruebas del acceso SQLite de solo lectura."""

from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.clean_orders import FIELDNAMES, clean_orders
from app.create_views import create_views
from app.db import (
    DatabaseUnavailableError,
    QueryExecutionError,
    QueryTimeoutError,
    execute_readonly,
    read_schema,
)


def order(
    order_id: str,
    order_date: str,
    country: str,
    amount: str,
) -> dict[str, str]:
    return {
        "order_id": order_id,
        "order_date": order_date,
        "customer": "Ana Torres",
        "country": country,
        "product": "Producto de prueba",
        "amount": amount,
        "status": "delivered",
    }


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


class DatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.temp_path = Path(self._temp_dir.name)
        self.csv_path = self.temp_path / "orders.csv"
        self.db_path = self.temp_path / "orders.db"
        rows = [
            order("ORD-1", "2024-01-01", "España", "100.00"),
            order("ORD-2", "2024-01-02", "España", "200.00"),
            order("ORD-3", "2024-02-01", "México", "300.00"),
            order("ORD-4", "2024-02-02", "Chile", "400.00"),
            order("ORD-5", "2025-01-01", "Chile", "500.00"),
        ]
        write_csv(self.csv_path, rows)
        clean_orders(self.csv_path, self.db_path)

    def test_read_schema_includes_allowed_objects(self) -> None:
        create_views(self.db_path)

        schema = read_schema(self.db_path)

        self.assertIn("CREATE TABLE orders", schema)
        self.assertIn("sales_by_month_country", schema)
        self.assertIn("top_10_customers", schema)

    def test_readonly_connection_rejects_writes(self) -> None:
        write_statements = [
            "INSERT INTO orders (order_id) VALUES ('ORD-X')",
            "UPDATE orders SET amount = 0",
            "DELETE FROM orders",
            "DROP TABLE orders",
        ]
        for sql in write_statements:
            with self.subTest(sql=sql):
                with self.assertRaises(QueryExecutionError):
                    execute_readonly(self.db_path, sql)

    def test_returns_rows_and_truncates(self) -> None:
        result = execute_readonly(
            self.db_path,
            "SELECT order_id FROM orders ORDER BY order_id",
            max_rows=2,
        )

        self.assertEqual(result.columns, ["order_id"])
        self.assertEqual(
            [row["order_id"] for row in result.rows],
            ["ORD-1", "ORD-2"],
        )
        self.assertTrue(result.truncated)

    def test_does_not_truncate_exact_limit(self) -> None:
        result = execute_readonly(
            self.db_path,
            "SELECT order_id FROM orders ORDER BY order_id",
            max_rows=5,
        )

        self.assertEqual(len(result.rows), 5)
        self.assertFalse(result.truncated)

    def test_duplicate_column_names_are_disambiguated(self) -> None:
        result = execute_readonly(
            self.db_path,
            "SELECT 1 AS value, 2 AS value",
        )

        self.assertEqual(result.columns, ["value", "value_2"])
        self.assertEqual(result.rows, [{"value": 1, "value_2": 2}])

    def test_missing_database_raises_error(self) -> None:
        with self.assertRaises(DatabaseUnavailableError):
            execute_readonly(self.temp_path / "missing.db", "SELECT 1")

    def test_database_without_orders_raises_error(self) -> None:
        empty_db = self.temp_path / "empty.db"
        sqlite3.connect(empty_db).close()

        with self.assertRaises(DatabaseUnavailableError):
            read_schema(empty_db)

    def test_invalid_column_raises_query_error(self) -> None:
        with self.assertRaises(QueryExecutionError):
            execute_readonly(
                self.db_path,
                "SELECT nonexistent_column FROM orders",
            )

    def test_timeout_interrupts_long_query(self) -> None:
        with self.assertRaises(QueryTimeoutError):
            execute_readonly(
                self.db_path,
                (
                    "WITH RECURSIVE counter(value) AS ("
                    "VALUES(1) "
                    "UNION ALL "
                    "SELECT value + 1 FROM counter WHERE value < 100000000"
                    ") SELECT SUM(value) FROM counter"
                ),
                timeout=0.001,
            )

    def test_special_characters_in_database_path(self) -> None:
        special_db = self.temp_path / "db with spaces #1.db"
        clean_orders(self.csv_path, special_db)

        result = execute_readonly(
            special_db,
            "SELECT COUNT(*) AS total FROM orders",
        )

        self.assertEqual(result.rows, [{"total": 5}])


if __name__ == "__main__":
    unittest.main()
