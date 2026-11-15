"""Pruebas del endpoint /query con un LLM simulado."""

from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.clean_orders import FIELDNAMES, clean_orders
from app.create_views import create_views
from app.llm import LLMError, OpenAISQLGenerator, build_system_prompt
from app.service import QueryService


def order(
    order_id: str,
    order_date: str,
    customer: str,
    country: str,
    amount: str,
) -> dict[str, str]:
    return {
        "order_id": order_id,
        "order_date": order_date,
        "customer": customer,
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


class FakeGenerator:
    def __init__(
        self,
        sql: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self._sql = sql
        self._error = error
        self.questions: list[str] = []
        self.schemas: list[str] = []

    def generate_sql(self, question: str, schema: str) -> str:
        self.questions.append(question)
        self.schemas.append(schema)
        if self._error is not None:
            raise self._error
        if self._sql is None:
            raise AssertionError("el test no configuró un SQL")
        return self._sql


class QueryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.temp_path = Path(self._temp_dir.name)
        self.csv_path = self.temp_path / "orders.csv"
        self.db_path = self.temp_path / "orders.db"
        write_csv(
            self.csv_path,
            [
                order("ORD-1", "2024-01-01", "Ana Torres", "España", "100.00"),
                order("ORD-2", "2024-01-02", "Luis Pérez", "España", "200.00"),
                order("ORD-3", "2024-02-01", "Carlos Mendoza", "México", "300.00"),
                order("ORD-4", "2024-02-02", "Ana Torres", "Chile", "400.00"),
                order("ORD-5", "2025-01-01", "Ana Torres", "Chile", "500.00"),
            ],
        )
        clean_orders(self.csv_path, self.db_path)
        create_views(self.db_path)

    def make_client(
        self,
        sql: str | None = None,
        error: Exception | None = None,
        max_rows: int = 200,
        db_path: Path | None = None,
    ) -> tuple[TestClient, FakeGenerator]:
        generator = FakeGenerator(sql=sql, error=error)
        service = QueryService(
            generator=generator,
            db_path=db_path or self.db_path,
            max_result_rows=max_rows,
            sql_timeout_seconds=2.0,
        )
        client = TestClient(create_app(service=service))
        self.addCleanup(client.close)
        return client, generator

    def test_valid_question_returns_results(self) -> None:
        client, generator = self.make_client(
            sql=(
                "SELECT country, SUM(amount) AS total_sales "
                "FROM orders GROUP BY country ORDER BY country"
            )
        )

        response = client.post(
            "/query",
            json={"question": "¿Cuánto se vendió por país?"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["question"], "¿Cuánto se vendió por país?")
        self.assertEqual(payload["columns"], ["country", "total_sales"])
        self.assertEqual(
            [row["country"] for row in payload["rows"]],
            ["Chile", "España", "México"],
        )
        self.assertEqual(payload["row_count"], 3)
        self.assertFalse(payload["truncated"])
        self.assertIn("SELECT", payload["sql"])
        self.assertEqual(generator.questions, ["¿Cuánto se vendió por país?"])
        self.assertIn("CREATE TABLE orders", generator.schemas[0])

    def test_query_over_authorized_view(self) -> None:
        client, _ = self.make_client(
            sql=(
                "SELECT year, month, country, total_orders "
                "FROM sales_by_month_country "
                "ORDER BY year, month, country"
            )
        )

        response = client.post(
            "/query",
            json={"question": "Ventas por mes y país"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["columns"],
            ["year", "month", "country", "total_orders"],
        )
        self.assertEqual(payload["row_count"], 4)

    def test_executes_canonical_sql(self) -> None:
        client, _ = self.make_client(
            sql="select country from orders; -- comentario"
        )

        response = client.post("/query", json={"question": "Países"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sql"], "SELECT country FROM orders")
        self.assertEqual(payload["row_count"], 5)

    def test_write_statement_is_rejected_and_database_unchanged(self) -> None:
        client, _ = self.make_client(sql="DROP TABLE orders")

        response = client.post("/query", json={"question": "Borra todo"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "unauthorized_query")
        connection = sqlite3.connect(self.db_path)
        try:
            total = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(total, 5)

    def test_multiple_statements_are_rejected(self) -> None:
        client, _ = self.make_client(
            sql="SELECT 1; DROP TABLE orders"
        )

        response = client.post("/query", json={"question": "Dos sentencias"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "unauthorized_query")

    def test_multiple_selects_are_rejected(self) -> None:
        client, _ = self.make_client(sql="SELECT 1; SELECT 2")

        response = client.post("/query", json={"question": "Dos selects"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_sql")

    def test_unauthorized_table_is_rejected(self) -> None:
        client, _ = self.make_client(sql="SELECT * FROM users")

        response = client.post("/query", json={"question": "Usuarios"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "unauthorized_query")

    def test_invalid_sql_is_rejected(self) -> None:
        client, _ = self.make_client(sql="SELECT * FROM")

        response = client.post("/query", json={"question": "SQL roto"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_sql")

    def test_select_into_is_rejected(self) -> None:
        client, _ = self.make_client(
            sql="SELECT * INTO backup FROM orders"
        )

        response = client.post("/query", json={"question": "Copia"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "unauthorized_query")

    def test_root_union_is_rejected(self) -> None:
        client, _ = self.make_client(sql="SELECT 1 UNION SELECT 2")

        response = client.post("/query", json={"question": "Unión"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_sql")

    def test_llm_error_is_reported(self) -> None:
        client, _ = self.make_client(error=LLMError("fallo simulado"))

        response = client.post("/query", json={"question": "Pregunta"})

        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertEqual(payload["detail"]["code"], "llm_error")
        self.assertNotIn(str(self.temp_path), response.text)

    def test_missing_database_returns_503_without_calling_llm(self) -> None:
        missing_db = self.temp_path / "missing.db"
        client, generator = self.make_client(
            sql="SELECT 1",
            db_path=missing_db,
        )

        response = client.post("/query", json={"question": "Pregunta"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"]["code"],
            "database_unavailable",
        )
        self.assertEqual(generator.questions, [])
        self.assertNotIn(str(self.temp_path), response.text)

    def test_truncates_results(self) -> None:
        client, _ = self.make_client(
            sql="SELECT order_id FROM orders ORDER BY order_id",
            max_rows=2,
        )

        response = client.post("/query", json={"question": "Pedidos"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["row_count"], 2)
        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["rows"]), 2)

    def test_blank_question_is_rejected(self) -> None:
        client, _ = self.make_client(sql="SELECT 1")

        response = client.post("/query", json={"question": "   "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_request")

    def test_extra_fields_are_rejected(self) -> None:
        client, _ = self.make_client(sql="SELECT 1")

        response = client.post(
            "/query",
            json={"question": "Pregunta", "sql": "SELECT 1"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_request")

    def test_too_long_question_is_rejected(self) -> None:
        client, _ = self.make_client(sql="SELECT 1")

        response = client.post(
            "/query",
            json={"question": "a" * 1001},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_request")

    def test_app_starts_without_api_key_but_reports_llm_error(self) -> None:
        self.assertIsNotNone(create_app())

        generator = OpenAISQLGenerator(
            api_key=None,
            model="gpt-4o-mini",
        )
        with self.assertRaises(LLMError):
            generator.generate_sql("Pregunta", "CREATE TABLE orders (...)")

    def test_system_prompt_contains_schema_and_rules(self) -> None:
        prompt = build_system_prompt("CREATE TABLE orders (order_id TEXT);")

        self.assertIn("CREATE TABLE orders", prompt)
        self.assertIn("SELECT", prompt)
        self.assertIn("No uses SELECT INTO", prompt)


if __name__ == "__main__":
    unittest.main()
