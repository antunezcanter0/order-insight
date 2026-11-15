"""Pruebas de la limpieza de pedidos y de la persistencia en SQLite."""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from app import clean_orders as clean_orders_module
from app.clean_orders import (
    FIELDNAMES,
    CleanOrdersError,
    clean_orders,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_CSV = PROJECT_ROOT / "data" / "orders_raw.csv"


def valid_row(order_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "order_id": order_id,
        "order_date": "2024-05-17",
        "customer": "Ana Torres",
        "country": "España",
        "product": "Laptop Pro 14",
        "amount": "1000.00",
        "status": "delivered",
    }
    row.update(overrides)
    return row


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def read_orders(db_path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute("SELECT * FROM orders ORDER BY order_id"))
    finally:
        connection.close()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CleanOrdersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.temp_path = Path(self._temp_dir.name)
        self.csv_path = self.temp_path / "orders.csv"
        self.db_path = self.temp_path / "orders.db"

    def run_clean(self, rows: list[dict[str, str]]):
        write_csv(self.csv_path, rows)
        return clean_orders(self.csv_path, self.db_path)

    def test_duplicate_order_id_is_removed_keeping_first(self) -> None:
        report = self.run_clean(
            [
                valid_row("ORD-1", amount="10.00"),
                valid_row("ORD-1", amount="99.99"),
            ]
        )

        self.assertEqual(report.duplicates, 1)
        self.assertEqual(report.stored, 1)
        orders = read_orders(self.db_path)
        self.assertEqual(len(orders), 1)
        self.assertAlmostEqual(orders[0]["amount"], 10.0)

    def test_duplicate_with_invalid_first_row_keeps_valid_second(self) -> None:
        report = self.run_clean(
            [
                valid_row("ORD-1", order_date="2025/01/01"),
                valid_row("ORD-1", order_date="2024-01-01"),
            ]
        )

        self.assertEqual(report.invalid_dates, 1)
        self.assertEqual(report.duplicates, 0)
        self.assertEqual(report.stored, 1)
        orders = read_orders(self.db_path)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["order_date"], "2024-01-01")

    def test_invalid_dates_are_excluded(self) -> None:
        invalid_dates = [
            "2025/12/15",
            "July 23, 2025",
            "24/03/2025",
            "2024-02-30",
            "20240101",
        ]
        rows = [valid_row("ORD-OK")]
        rows.extend(
            valid_row(f"ORD-BAD-{index}", order_date=value)
            for index, value in enumerate(invalid_dates)
        )

        report = self.run_clean(rows)

        self.assertEqual(report.invalid_dates, len(invalid_dates))
        self.assertEqual(report.stored, 1)
        orders = read_orders(self.db_path)
        self.assertEqual([order["order_id"] for order in orders], ["ORD-OK"])

    def test_invalid_amounts_are_excluded(self) -> None:
        invalid_amounts = ["", "   ", "abc", "NaN", "-5", "inf"]
        rows = [valid_row("ORD-OK")]
        rows.extend(
            valid_row(f"ORD-BAD-{index}", amount=value)
            for index, value in enumerate(invalid_amounts)
        )

        report = self.run_clean(rows)

        self.assertEqual(report.invalid_amounts, len(invalid_amounts))
        self.assertEqual(report.stored, 1)
        orders = read_orders(self.db_path)
        self.assertEqual([order["order_id"] for order in orders], ["ORD-OK"])

    def test_missing_required_fields_are_excluded(self) -> None:
        report = self.run_clean(
            [
                valid_row("ORD-1"),
                valid_row("ORD-2", customer="   "),
                valid_row("ORD-3", status=""),
            ]
        )

        self.assertEqual(report.invalid_fields, 2)
        self.assertEqual(report.stored, 1)

    def test_valid_rows_are_persisted_in_sqlite(self) -> None:
        report = self.run_clean(
            [
                valid_row("ORD-1", amount="12.50"),
                valid_row("ORD-2", order_date="2024-02-29", amount="0.00"),
            ]
        )

        self.assertEqual(report.stored, 2)
        connection = sqlite3.connect(self.db_path)
        try:
            columns = {
                row[1]: row
                for row in connection.execute("PRAGMA table_info(orders)")
            }
        finally:
            connection.close()

        self.assertEqual(
            list(columns),
            [
                "order_id",
                "order_date",
                "customer",
                "country",
                "product",
                "amount",
                "status",
            ],
        )
        self.assertEqual(columns["order_id"][5], 1)  # clave primaria
        self.assertEqual(columns["amount"][2], "REAL")

        orders = read_orders(self.db_path)
        self.assertEqual(orders[0]["order_id"], "ORD-1")
        self.assertEqual(orders[0]["order_date"], "2024-05-17")
        self.assertAlmostEqual(orders[0]["amount"], 12.5)
        self.assertEqual(orders[1]["order_date"], "2024-02-29")

    def test_report_totals_add_up(self) -> None:
        report = self.run_clean(
            [
                valid_row("ORD-1"),
                valid_row("ORD-1"),
                valid_row("ORD-2", order_date="not-a-date"),
                valid_row("ORD-3", amount=""),
                valid_row("ORD-4", product=""),
            ]
        )

        self.assertEqual(report.total_rows, 5)
        self.assertEqual(report.stored, 1)
        self.assertEqual(report.duplicates, 1)
        self.assertEqual(report.invalid_dates, 1)
        self.assertEqual(report.invalid_amounts, 1)
        self.assertEqual(report.invalid_fields, 1)
        self.assertEqual(
            report.total_rows,
            report.stored + report.discarded,
        )

    def test_rerun_rebuilds_table_with_current_csv(self) -> None:
        self.run_clean([valid_row("ORD-OLD")])

        report = self.run_clean([valid_row("ORD-NEW")])

        self.assertEqual(report.stored, 1)
        orders = read_orders(self.db_path)
        self.assertEqual([order["order_id"] for order in orders], ["ORD-NEW"])

    def test_failure_rolls_back_and_keeps_previous_data(self) -> None:
        self.run_clean([valid_row("ORD-OLD")])

        write_csv(self.csv_path, [valid_row("ORD-NEW")])
        with mock.patch.object(
            clean_orders_module,
            "_insert_orders",
            side_effect=RuntimeError("fallo simulado"),
        ):
            with self.assertRaises(RuntimeError):
                clean_orders(self.csv_path, self.db_path)

        orders = read_orders(self.db_path)
        self.assertEqual([order["order_id"] for order in orders], ["ORD-OLD"])

    def test_missing_columns_raise_error_without_creating_db(self) -> None:
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([field for field in FIELDNAMES if field != "amount"])
            writer.writerow(
                [
                    "ORD-1",
                    "2024-05-17",
                    "Ana Torres",
                    "España",
                    "Laptop Pro 14",
                    "delivered",
                ]
            )

        with self.assertRaises(CleanOrdersError):
            clean_orders(self.csv_path, self.db_path)

        self.assertFalse(self.db_path.exists())

    def test_cli_runs_and_creates_database(self) -> None:
        write_csv(self.csv_path, [valid_row("ORD-1")])

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                ["--input", str(self.csv_path), "--db", str(self.db_path)]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(self.db_path.exists())
        self.assertIn("Registros válidos guardados: 1", output.getvalue())

    def test_real_dataset_summary(self) -> None:
        if not REAL_CSV.exists():
            self.skipTest("no existe data/orders_raw.csv")
        before = file_digest(REAL_CSV)

        report = clean_orders(REAL_CSV, self.db_path)

        self.assertEqual(report.total_rows, 1000)
        self.assertEqual(report.duplicates, 20)
        self.assertEqual(report.invalid_dates, 20)
        self.assertEqual(report.invalid_amounts, 20)
        self.assertEqual(report.invalid_fields, 0)
        self.assertEqual(report.stored, 940)
        self.assertEqual(len(read_orders(self.db_path)), 940)
        self.assertEqual(file_digest(REAL_CSV), before)


if __name__ == "__main__":
    unittest.main()
