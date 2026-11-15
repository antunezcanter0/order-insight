"""Pruebas del generador de pedidos de ejemplo."""

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.generate_orders import (
    DEFAULT_ROWS,
    DEFAULT_SEED,
    FIELDNAMES,
    generate_orders,
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GenerateOrdersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.temp_path = Path(self._temp_dir.name)

    def test_generates_requested_number_of_rows(self) -> None:
        output = self.temp_path / "orders.csv"
        result = generate_orders(rows=250, seed=DEFAULT_SEED, output_path=output)

        rows = read_rows(output)
        self.assertEqual(result.rows, 250)
        self.assertEqual(len(rows), 250)
        self.assertEqual(list(rows[0].keys()), FIELDNAMES)
        self.assertEqual(output.exists(), True)

    def test_defaults_generate_at_least_one_thousand_orders(self) -> None:
        output = self.temp_path / "orders.csv"
        result = generate_orders(output_path=output)

        self.assertEqual(result.rows, DEFAULT_ROWS)
        self.assertEqual(len(read_rows(output)), DEFAULT_ROWS)

    def test_same_seed_produces_identical_files(self) -> None:
        first = self.temp_path / "first.csv"
        second = self.temp_path / "second.csv"

        generate_orders(seed=DEFAULT_SEED, output_path=first)
        generate_orders(seed=DEFAULT_SEED, output_path=second)

        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_different_seed_produces_different_files(self) -> None:
        first = self.temp_path / "first.csv"
        second = self.temp_path / "second.csv"

        generate_orders(seed=1, output_path=first)
        generate_orders(seed=2, output_path=second)

        self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_intentional_errors_are_present_and_reported(self) -> None:
        output = self.temp_path / "orders.csv"
        result = generate_orders(seed=DEFAULT_SEED, output_path=output)
        rows = read_rows(output)

        order_ids = [row["order_id"] for row in rows]
        duplicate_rows = len(order_ids) - len(set(order_ids))
        malformed_dates = []
        missing_amounts = []
        for row in rows:
            try:
                date.fromisoformat(row["order_date"])
            except ValueError:
                malformed_dates.append(row)
            if row["amount"] == "":
                missing_amounts.append(row)

        self.assertGreater(duplicate_rows, 0)
        self.assertGreater(len(malformed_dates), 0)
        self.assertGreater(len(missing_amounts), 0)

        self.assertEqual(result.duplicate_ids, duplicate_rows)
        self.assertEqual(result.malformed_dates, len(malformed_dates))
        self.assertEqual(result.missing_amounts, len(missing_amounts))
        self.assertEqual(
            result.total_anomalies,
            duplicate_rows + len(malformed_dates) + len(missing_amounts),
        )

    def test_valid_rows_keep_expected_format(self) -> None:
        output = self.temp_path / "orders.csv"
        generate_orders(seed=DEFAULT_SEED, output_path=output)

        for row in read_rows(output):
            if row["amount"]:
                self.assertRegex(row["amount"], r"^\d+\.\d{2}$")
            value = row["order_date"]
            is_iso = (
                len(value) == 10 and value[4] == "-" and value[7] == "-"
            )
            if is_iso:
                date.fromisoformat(value)


if __name__ == "__main__":
    unittest.main()
