"""Acceso de solo lectura a la base de datos SQLite."""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.sql_guard import ALLOWED_TABLES


class DatabaseError(Exception):
    """Error de acceso a la base de datos."""


class DatabaseUnavailableError(DatabaseError):
    """La base de datos no existe o no se puede abrir."""


class QueryExecutionError(DatabaseError):
    """La consulta no se pudo ejecutar."""


class QueryTimeoutError(DatabaseError):
    """La consulta excedió el tiempo máximo permitido."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool


def _readonly_uri(db_path: Path) -> str:
    # Path.as_uri() genera una URI file:/// absoluta y codifica caracteres
    # especiales, necesarios para el modo URI de SQLite.
    return f"{db_path.resolve().as_uri()}?mode=ro"


def _open_readonly(db_path: Path, timeout: float) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            _readonly_uri(db_path),
            uri=True,
            timeout=timeout,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise DatabaseUnavailableError(
            "no se pudo abrir la base de datos en modo de solo lectura"
        ) from exc
    return connection


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _unique_columns(description: Any) -> list[str]:
    columns: list[str] = []
    used: set[str] = set()
    for index, item in enumerate(description or []):
        base = item[0] or f"column_{index + 1}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        columns.append(candidate)
    return columns


def read_schema(
    db_path: Path | str,
    timeout: float = 5.0,
) -> str:
    """Devuelve el DDL de la tabla y las vistas autorizadas."""
    path = Path(db_path)
    connection = _open_readonly(path, timeout)
    placeholders = ", ".join("?" for _ in ALLOWED_TABLES)
    try:
        rows = connection.execute(
            f"""
            SELECT type, name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'view')
              AND name IN ({placeholders})
            ORDER BY type DESC, name
            """,
            tuple(sorted(ALLOWED_TABLES)),
        ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseUnavailableError(
            "no se pudo leer el esquema de la base de datos"
        ) from exc
    finally:
        connection.close()

    names = {row["name"] for row in rows}
    if "orders" not in names:
        raise DatabaseUnavailableError(
            "la base de datos no contiene el esquema esperado"
        )

    return "\n\n".join(
        f"-- {row['type']} {row['name']}\n{row['sql']};"
        for row in rows
        if row["sql"]
    )


def execute_readonly(
    db_path: Path | str,
    sql: str,
    max_rows: int = 200,
    timeout: float = 5.0,
) -> QueryResult:
    """Ejecuta una consulta validada con conexión de solo lectura."""
    if max_rows <= 0:
        raise ValueError("max_rows debe ser mayor que cero")
    if timeout <= 0:
        raise ValueError("timeout debe ser mayor que cero")

    connection = _open_readonly(Path(db_path), timeout)
    cursor: sqlite3.Cursor | None = None
    deadline = time.monotonic() + timeout

    def _abort_if_expired() -> int:
        return 1 if time.monotonic() > deadline else 0

    try:
        connection.set_progress_handler(_abort_if_expired, 10_000)
        cursor = connection.execute(sql)
        fetched = cursor.fetchmany(max_rows + 1)
        columns = _unique_columns(cursor.description)
        truncated = len(fetched) > max_rows
        raw_rows = fetched[:max_rows]
        rows = [
            {
                column: _json_value(value)
                for column, value in zip(columns, row)
            }
            for row in raw_rows
        ]
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
        )
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise QueryTimeoutError(
                "la consulta excedió el tiempo máximo de ejecución"
            ) from exc
        raise QueryExecutionError(
            "la consulta SQL no se pudo ejecutar"
        ) from exc
    except sqlite3.Error as exc:
        raise QueryExecutionError(
            "la consulta SQL no se pudo ejecutar"
        ) from exc
    finally:
        if cursor is not None:
            cursor.close()
        connection.set_progress_handler(None, 0)
        connection.close()
