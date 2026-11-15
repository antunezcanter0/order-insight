"""Orquestación de pregunta en lenguaje natural a resultados SQL."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db import execute_readonly, read_schema
from app.llm import SQLGenerator
from app.sql_guard import validate_sql


@dataclass(frozen=True)
class QueryAnswer:
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool


class QueryService:
    """Coordina generación, validación y ejecución de la consulta."""

    def __init__(
        self,
        generator: SQLGenerator,
        db_path: Path | str,
        max_result_rows: int = 200,
        sql_timeout_seconds: float = 5.0,
    ) -> None:
        self._generator = generator
        self._db_path = Path(db_path)
        self._max_result_rows = max_result_rows
        self._sql_timeout_seconds = sql_timeout_seconds

    def answer(self, question: str) -> QueryAnswer:
        schema = read_schema(
            self._db_path,
            timeout=self._sql_timeout_seconds,
        )
        generated_sql = self._generator.generate_sql(question, schema)
        validated_sql = validate_sql(generated_sql)
        result = execute_readonly(
            self._db_path,
            validated_sql,
            max_rows=self._max_result_rows,
            timeout=self._sql_timeout_seconds,
        )
        return QueryAnswer(
            sql=validated_sql,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
        )
