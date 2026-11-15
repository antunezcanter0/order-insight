"""Pruebas de las vistas SQL de análisis."""

from __future__ import annotations

import csv
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from app.clean_orders import FIELDNAMES, clean_orders
from app.create_views import VIEW_NAMES, ViewsError, create_views, main


def order(
    order_id: str,
    order_date: str,
    customer: str,
    country: str,
    amount: str,
    status: str = "delivered",
) -> dict[str, str]:
    return {
        "order_id": order_id,
        "order_date": order_date,
        "customer": customer,
        "country": country,
        "product": "Producto de prueba",
        "amount": amount,
        "status": status,
    }


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def fetch_all(db_path: Path, query: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(query))
    finally:
        connection.close()


def stored_views(db_path: Path) -> list[str]:
    rows = fetch_all(
        db_path,
        "SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name",
    )
    return [row["name"] for row in rows]


class CreateViewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.temp_path = Path(self._temp_dir.name)
        self.csv_path = self.temp_path / "orders.csv"
        self.db_path = self.temp_path / "orders.db"

    def populate(self, rows: list[dict[str, str]]) -> None:
        write_csv(self.csv_path, rows)
        clean_orders(self.csv_path, self.db_path)

    def test_creates_both_views(self) -> None:
        self.populate([order("ORD-1", "2024-01-05", "Ana", "España", "10.00")])

        created = create_views(self.db_path)

        self.assertEqual(created, list(VIEW_NAMES))
        self.assertEqual(sorted(stored_views(self.db_path)), sorted(VIEW_NAMES))

    def test_view_columns(self) -> None:
        self.populate([order("ORD-1", "2024-01-05", "Ana", "España", "10.00")])
        create_views(self.db_path)

        sales_columns = [
            row["name"]
            for row in fetch_all(
                self.db_path,
                "PRAGMA table_info(sales_by_month_country)",
            )
        ]
        customers_columns = [
            row["name"]
            for row in fetch_all(
                self.db_path,
                "PRAGMA table_info(top_10_customers)",
            )
        ]

        self.assertEqual(
            sales_columns,
            [
                "year",
                "month",
                "country",
                "total_orders",
                "total_sales",
                "average_order_value",
            ],
        )
        self.assertEqual(
            customers_columns,
            [
                "customer",
                "total_orders",
                "total_sales",
                "average_order_value",
            ],
        )

    def test_create_views_is_idempotent(self) -> None:
        self.populate([order("ORD-1", "2024-01-05", "Ana", "España", "10.00")])

        create_views(self.db_path)
        create_views(self.db_path)

        rows = fetch_all(
            self.db_path,
            """
            SELECT name, COUNT(*) AS occurrences
            FROM sqlite_master
            WHERE type = 'view'
            GROUP BY name
            ORDER BY name
            """,
        )
        self.assertEqual(
            [(row["name"], row["occurrences"]) for row in rows],
            [(name, 1) for name in sorted(VIEW_NAMES)],
        )

    def test_sales_by_month_country_aggregation(self) -> None:
        self.populate(
            [
                order("ORD-1", "2024-01-05", "Ana", "España", "100.00"),
                order("ORD-2", "2024-01-20", "Luis", "España", "200.00"),
                order("ORD-3", "2024-01-10", "Carlos", "México", "50.00"),
                order("ORD-4", "2024-02-02", "Ana", "España", "400.00"),
            ]
        )
        create_views(self.db_path)

        rows = fetch_all(
            self.db_path,
            """
            SELECT year, month, country, total_orders, total_sales,
                   average_order_value
            FROM sales_by_month_country
            ORDER BY year, month, country
            """,
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            (rows[0]["year"], rows[0]["month"], rows[0]["country"]),
            (2024, 1, "España"),
        )
        self.assertEqual(rows[0]["total_orders"], 2)
        self.assertAlmostEqual(rows[0]["total_sales"], 300.0)
        self.assertAlmostEqual(rows[0]["average_order_value"], 150.0)
        self.assertEqual(
            (rows[1]["month"], rows[1]["country"]),
            (1, "México"),
        )
        self.assertEqual(rows[1]["total_orders"], 1)
        self.assertAlmostEqual(rows[1]["total_sales"], 50.0)
        self.assertAlmostEqual(rows[1]["average_order_value"], 50.0)
        self.assertEqual(
            (rows[2]["month"], rows[2]["country"]),
            (2, "España"),
        )
        self.assertEqual(rows[2]["total_orders"], 1)
        self.assertAlmostEqual(rows[2]["total_sales"], 400.0)

    def test_sales_by_month_country_separates_years(self) -> None:
        self.populate(
            [
                order("ORD-1", "2023-06-01", "Ana", "España", "10.00"),
                order("ORD-2", "2024-06-01", "Ana", "España", "20.00"),
            ]
        )
        create_views(self.db_path)

        rows = fetch_all(
            self.db_path,
            """
            SELECT year, month, country, total_orders, total_sales
            FROM sales_by_month_country
            ORDER BY year
            """,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["year"], rows[0]["month"]), (2023, 6))
        self.assertEqual((rows[1]["year"], rows[1]["month"]), (2024, 6))
        self.assertIsInstance(rows[0]["year"], int)
        self.assertIsInstance(rows[0]["month"], int)

    def test_top_10_customers_returns_ten_ordered_by_sales(self) -> None:
        rows = [
            order(
                f"ORD-{index:03d}",
                "2024-03-01",
                f"Cliente {index:02d}",
                "España",
                f"{1300 - index * 100}.00",
            )
            for index in range(1, 13)
        ]
        self.populate(rows)
        create_views(self.db_path)

        result = fetch_all(
            self.db_path,
            "SELECT customer, total_orders, total_sales FROM top_10_customers",
        )

        self.assertEqual(len(result), 10)
        self.assertEqual(
            [row["customer"] for row in result],
            [f"Cliente {index:02d}" for index in range(1, 11)],
        )
        totals = [row["total_sales"] for row in result]
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertNotIn("Cliente 11", [row["customer"] for row in result])
        self.assertNotIn("Cliente 12", [row["customer"] for row in result])

    def test_top_10_customers_tie_breaks_by_name(self) -> None:
        self.populate(
            [
                order("ORD-1", "2024-03-01", "Zoe Ruiz", "España", "500.00"),
                order("ORD-2", "2024-03-02", "Ana Gómez", "España", "500.00"),
                order("ORD-3", "2024-03-03", "Luis Pérez", "España", "100.00"),
            ]
        )
        create_views(self.db_path)

        rows = fetch_all(
            self.db_path,
            "SELECT customer, total_sales FROM top_10_customers",
        )

        self.assertEqual(
            [(row["customer"], row["total_sales"]) for row in rows],
            [("Ana Gómez", 500.0), ("Zoe Ruiz", 500.0), ("Luis Pérez", 100.0)],
        )

    def test_top_10_customers_aggregation(self) -> None:
        self.populate(
            [
                order("ORD-1", "2024-03-01", "Ana", "España", "100.00"),
                order("ORD-2", "2024-03-02", "Ana", "España", "200.00"),
                order("ORD-3", "2024-03-03", "Ana", "España", "300.00"),
                order("ORD-4", "2024-03-04", "Luis", "España", "50.00"),
            ]
        )
        create_views(self.db_path)

        rows = fetch_all(
            self.db_path,
            """
            SELECT customer, total_orders, total_sales, average_order_value
            FROM top_10_customers
            WHERE customer = 'Ana'
            """,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_orders"], 3)
        self.assertAlmostEqual(rows[0]["total_sales"], 600.0)
        self.assertAlmostEqual(rows[0]["average_order_value"], 200.0)

    def test_create_views_does_not_modify_orders(self) -> None:
        self.populate(
            [
                order("ORD-1", "2024-01-05", "Ana", "España", "100.00"),
                order("ORD-2", "2024-02-05", "Luis", "México", "200.00"),
            ]
        )
        before = fetch_all(self.db_path, "SELECT * FROM orders ORDER BY order_id")

        create_views(self.db_path)

        after = fetch_all(self.db_path, "SELECT * FROM orders ORDER BY order_id")
        self.assertEqual(
            [tuple(row) for row in before],
            [tuple(row) for row in after],
        )

    def test_views_work_after_clean_orders_rebuilds_table(self) -> None:
        self.populate([order("ORD-OLD", "2024-01-05", "Ana", "España", "100.00")])
        create_views(self.db_path)

        write_csv(
            self.csv_path,
            [order("ORD-NEW", "2025-03-08", "Luis", "Chile", "999.00")],
        )
        clean_orders(self.csv_path, self.db_path)

        self.assertEqual(sorted(stored_views(self.db_path)), sorted(VIEW_NAMES))
        sales = fetch_all(
            self.db_path,
            "SELECT year, month, country, total_sales FROM sales_by_month_country",
        )
        self.assertEqual(len(sales), 1)
        self.assertEqual(
            (sales[0]["year"], sales[0]["month"], sales[0]["country"]),
            (2025, 3, "Chile"),
        )
        self.assertAlmostEqual(sales[0]["total_sales"], 999.0)
        customers = fetch_all(
            self.db_path,
            "SELECT customer, total_sales FROM top_10_customers",
        )
        self.assertEqual(
            [(row["customer"], row["total_sales"]) for row in customers],
            [("Luis", 999.0)],
        )

    def test_missing_orders_table_raises_error(self) -> None:
        sqlite3.connect(self.db_path).close()

        with self.assertRaises(ViewsError):
            create_views(self.db_path)

        error = io.StringIO()
        with redirect_stderr(error):
            exit_code = main(["--db", str(self.db_path)])
        self.assertEqual(exit_code, 1)
        self.assertIn("orders", error.getvalue())

    def test_missing_database_raises_error(self) -> None:
        with self.assertRaises(ViewsError):
            create_views(self.db_path)

    def test_cli_creates_views(self) -> None:
        self.populate([order("ORD-1", "2024-01-05", "Ana", "España", "10.00")])

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--db", str(self.db_path)])

        self.assertEqual(exit_code, 0)
        self.assertEqual(sorted(stored_views(self.db_path)), sorted(VIEW_NAMES))
        for name in VIEW_NAMES:
            self.assertIn(name, output.getvalue())


if __name__ == "__main__":
    unittest.main()
