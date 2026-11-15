"""API FastAPI para consultas en lenguaje natural sobre los pedidos."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings
from app.db import (
    DatabaseUnavailableError,
    QueryExecutionError,
    QueryTimeoutError,
)
from app.llm import LLMError, OpenAISQLGenerator
from app.service import QueryService
from app.sql_guard import SQLValidationError, UnauthorizedQueryError

logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("la pregunta no puede estar vacía")
        return stripped


class QueryResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


def _error_response(
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )


def _build_default_service() -> QueryService:
    settings = Settings.from_env()
    generator = OpenAISQLGenerator(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )
    return QueryService(
        generator=generator,
        db_path=settings.db_path,
        max_result_rows=settings.max_result_rows,
        sql_timeout_seconds=settings.sql_timeout_seconds,
    )


def create_app(service: QueryService | None = None) -> FastAPI:
    """Crea la aplicación, permitiendo inyectar un servicio en pruebas."""
    application = FastAPI(
        title="OrderInsight API",
        version="1.0.0",
        description=(
            "Consultas en lenguaje natural sobre pedidos, con SQL "
            "generado por un LLM y validado antes de ejecutarse."
        ),
    )

    def get_query_service() -> QueryService:
        if service is not None:
            return service
        return _build_default_service()

    @application.exception_handler(UnauthorizedQueryError)
    async def _handle_unauthorized(
        request: Request,
        exc: UnauthorizedQueryError,
    ) -> JSONResponse:
        return _error_response(
            403,
            "unauthorized_query",
            str(exc),
        )

    @application.exception_handler(SQLValidationError)
    async def _handle_invalid_sql(
        request: Request,
        exc: SQLValidationError,
    ) -> JSONResponse:
        return _error_response(422, "invalid_sql", str(exc))

    @application.exception_handler(LLMError)
    async def _handle_llm_error(
        request: Request,
        exc: LLMError,
    ) -> JSONResponse:
        return _error_response(502, "llm_error", str(exc))

    @application.exception_handler(QueryTimeoutError)
    async def _handle_timeout(
        request: Request,
        exc: QueryTimeoutError,
    ) -> JSONResponse:
        return _error_response(504, "query_timeout", str(exc))

    @application.exception_handler(DatabaseUnavailableError)
    async def _handle_database_unavailable(
        request: Request,
        exc: DatabaseUnavailableError,
    ) -> JSONResponse:
        return _error_response(503, "database_unavailable", str(exc))

    @application.exception_handler(QueryExecutionError)
    async def _handle_query_execution(
        request: Request,
        exc: QueryExecutionError,
    ) -> JSONResponse:
        return _error_response(422, "invalid_sql", str(exc))

    @application.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            422,
            "invalid_request",
            "la petición no es válida",
        )

    @application.exception_handler(Exception)
    async def _handle_unexpected(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception("Error interno no controlado")
        return _error_response(
            500,
            "internal_error",
            "error interno del servidor",
        )

    @application.post(
        "/query",
        response_model=QueryResponse,
        responses={
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            504: {"model": ErrorResponse},
        },
    )
    def query(
        request: QueryRequest,
        query_service: QueryService = Depends(get_query_service),
    ) -> QueryResponse:
        answer = query_service.answer(request.question)
        return QueryResponse(
            question=request.question,
            sql=answer.sql,
            columns=answer.columns,
            rows=answer.rows,
            row_count=len(answer.rows),
            truncated=answer.truncated,
        )

    return application


app = create_app()
