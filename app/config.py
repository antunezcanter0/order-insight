"""Configuración de la API a partir de variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

DEFAULT_DB_PATH = Path("data/orders.db")
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_MAX_RESULT_ROWS = 200
DEFAULT_SQL_TIMEOUT_SECONDS = 5.0
DEFAULT_LLM_TIMEOUT_SECONDS = 20.0


def _positive_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: str, default: float) -> float:
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class Settings:
    """Configuración efectiva de la aplicación."""

    db_path: Path
    openai_api_key: str | None
    openai_model: str
    openai_base_url: str | None
    max_result_rows: int
    sql_timeout_seconds: float
    llm_timeout_seconds: float

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "Settings":
        if environ is None:
            load_dotenv()
            environ = os.environ

        api_key = (environ.get("OPENAI_API_KEY") or "").strip() or None
        base_url = (environ.get("OPENAI_BASE_URL") or "").strip() or None
        model = (
            environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        ).strip() or DEFAULT_OPENAI_MODEL
        db_path = Path(
            environ.get("ORDERS_DB_PATH") or DEFAULT_DB_PATH
        )

        return cls(
            db_path=db_path,
            openai_api_key=api_key,
            openai_model=model,
            openai_base_url=base_url,
            max_result_rows=_positive_int(
                environ.get("MAX_RESULT_ROWS", ""),
                DEFAULT_MAX_RESULT_ROWS,
            ),
            sql_timeout_seconds=_positive_float(
                environ.get("SQL_TIMEOUT_SECONDS", ""),
                DEFAULT_SQL_TIMEOUT_SECONDS,
            ),
            llm_timeout_seconds=_positive_float(
                environ.get("LLM_TIMEOUT_SECONDS", ""),
                DEFAULT_LLM_TIMEOUT_SECONDS,
            ),
        )
