"""Pruebas unitarias de la validación SQL con SQLGlot."""

from __future__ import annotations

import pytest

from app.sql_guard import (
    MAX_SQL_LENGTH,
    SQLValidationError,
    UnauthorizedQueryError,
    validate_sql,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "SELECT * FROM ORDERS",
        "SELECT country, SUM(amount) AS total_sales FROM orders GROUP BY country",
        "SELECT * FROM sales_by_month_country",
        "SELECT * FROM top_10_customers",
        "SELECT * FROM \"orders\"",
        "SELECT 1",
        (
            "WITH totals AS ("
            "SELECT country, SUM(amount) AS total_sales "
            "FROM orders GROUP BY country"
            ") SELECT * FROM totals"
        ),
        "SELECT * FROM (SELECT * FROM orders) AS o",
        (
            "SELECT o.order_id, t.total_sales "
            "FROM orders AS o "
            "JOIN top_10_customers AS t ON o.customer = t.customer"
        ),
        (
            "SELECT strftime('%Y', order_date) AS year, COUNT(*) AS total "
            "FROM orders GROUP BY 1"
        ),
        "SELECT * FROM orders WHERE product = 'users'",
        "SELECT * FROM (SELECT 1 UNION SELECT 2) AS nested",
        (
            "SELECT customer, "
            "ROW_NUMBER() OVER (ORDER BY SUM(amount) DESC) AS position "
            "FROM orders GROUP BY customer"
        ),
    ],
)
def test_accepts_authorized_selects(sql: str) -> None:
    canonical = validate_sql(sql)

    assert "SELECT" in canonical.upper()
    assert ";" not in canonical


def test_returns_canonical_sql() -> None:
    canonical = validate_sql("select * from orders; -- comentario")

    assert canonical == "SELECT * FROM orders"


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "SELECT * FROM",
        "SELEC * FROM orders",
        "SELECT 1; SELECT 2",
        "SELECT * FROM orders; SELECT * FROM orders",
        "SELECT 1 UNION SELECT 2",
        "SELECT 1 INTERSECT SELECT 2",
        "SELECT 1 EXCEPT SELECT 2",
        "SELECT 1" + "a" * MAX_SQL_LENGTH,
    ],
)
def test_rejects_invalid_or_multiple_statements(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (order_id) VALUES ('x')",
        "UPDATE orders SET amount = 0",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "CREATE TABLE backup AS SELECT * FROM orders",
        "ALTER TABLE orders ADD COLUMN extra TEXT",
        "PRAGMA table_info(orders)",
        "ATTACH DATABASE 'x.db' AS x",
        "VACUUM",
        "SELECT * INTO backup FROM orders",
        "SELECT * FROM users",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM pragma_table_info('orders')",
        "SELECT * FROM json_each('[1]')",
        "SELECT * FROM main.orders",
        "SELECT * FROM orders AS o JOIN users AS u ON 1 = 1",
        "WITH x AS (SELECT * FROM users) SELECT * FROM x",
        "SELECT * FROM (SELECT * FROM users) AS u",
        "SELECT load_extension('x') FROM orders",
        "SELECT readfile('/etc/passwd')",
        "SELECT randomblob(1000000) FROM orders",
        "SELECT 1; DROP TABLE orders",
    ],
)
def test_rejects_unauthorized_operations(sql: str) -> None:
    with pytest.raises(UnauthorizedQueryError):
        validate_sql(sql)


def test_rejects_non_string_sql() -> None:
    with pytest.raises(SQLValidationError):
        validate_sql(None)  # type: ignore[arg-type]
