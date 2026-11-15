"""Validación de SQL generado por un LLM mediante el AST de SQLGlot.

El SQL recibido nunca se considera confiable: se parsea, se comprueba que
sea una única consulta ``SELECT`` y se validan todas sus fuentes y
funciones antes de generar el SQL canónico que se ejecutará.
"""

from __future__ import annotations

from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

ALLOWED_TABLES: Final = frozenset(
    {
        "orders",
        "sales_by_month_country",
        "top_10_customers",
    }
)

FORBIDDEN_FUNCTIONS: Final = frozenset(
    {
        "LOAD_EXTENSION",
        "READFILE",
        "WRITEFILE",
        "EDIT",
        "FTS3_TOKENIZER",
        "SQLITE_DBPAGE",
        "SQLITE_DBDATA",
        "RANDOMBLOB",
        "ZEROBLOB",
    }
)

FORBIDDEN_NODE_TYPES: Final = tuple(
    getattr(exp, name)
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Create",
        "Drop",
        "Alter",
        "Attach",
        "Detach",
        "Transaction",
        "Commit",
        "Rollback",
        "Merge",
        "Copy",
        "Pragma",
        "Command",
        "Grant",
        "TruncateTable",
        "Use",
        "Set",
        "LoadData",
        "Export",
        "Describe",
        "Show",
        "Comment",
    )
    if hasattr(exp, name)
)

MAX_SQL_LENGTH = 10_000


class SQLValidationError(Exception):
    """El SQL generado no es válido o no se puede ejecutar con seguridad."""


class UnauthorizedQueryError(SQLValidationError):
    """El SQL solicita operaciones, tablas o funciones no autorizadas."""


def _reject_forbidden_nodes(expression: exp.Expression) -> None:
    for node in expression.walk():
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            raise UnauthorizedQueryError(
                "el SQL contiene operaciones de escritura o administrativas"
            )


def _validate_table(table: exp.Table) -> None:
    if table.args.get("db") or table.args.get("catalog"):
        raise UnauthorizedQueryError(
            "no se permiten nombres de tabla cualificados"
        )

    name = table.name.lower()
    if not name:
        raise UnauthorizedQueryError(
            "no se permiten funciones de tabla no autorizadas"
        )
    if name not in ALLOWED_TABLES:
        raise UnauthorizedQueryError(
            f"tabla o vista no autorizada: {table.name}"
        )


def _validate_sources(expression: exp.Expression) -> None:
    for scope in traverse_scope(expression):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                _validate_table(source)


def _function_name(function: exp.Func) -> str:
    rendered = function.sql(dialect="sqlite")
    return rendered.split("(", 1)[0].strip().upper()


def _validate_functions(expression: exp.Expression) -> None:
    for function in expression.find_all(exp.Func):
        name = _function_name(function)
        if name in FORBIDDEN_FUNCTIONS:
            raise UnauthorizedQueryError(
                f"función no permitida: {name.lower()}"
            )


def validate_sql(sql: str) -> str:
    """Valida ``sql`` y devuelve la versión canónica generada por SQLGlot.

    Lanza ``SQLValidationError`` si el texto no es una consulta válida y
    ``UnauthorizedQueryError`` si intenta acceder a operaciones, tablas,
    vistas o funciones no autorizadas.
    """
    if not isinstance(sql, str):
        raise SQLValidationError("el SQL debe ser una cadena de texto")

    candidate = sql.strip()
    if not candidate:
        raise SQLValidationError("el SQL está vacío")
    if len(candidate) > MAX_SQL_LENGTH:
        raise SQLValidationError("el SQL generado es demasiado largo")

    try:
        statements = [
            statement
            for statement in sqlglot.parse(candidate, read="sqlite")
            if statement is not None
            and not isinstance(statement, exp.Semicolon)
        ]
    except ParseError as exc:
        raise SQLValidationError(
            "no se pudo interpretar el SQL generado"
        ) from exc

    if not statements:
        raise SQLValidationError("el SQL está vacío")

    if len(statements) != 1:
        # Si alguna de las sentencias intenta escribir, se clasifica como
        # consulta no autorizada; si no, es simplemente un SQL inválido.
        for statement in statements:
            _reject_forbidden_nodes(statement)
        raise SQLValidationError(
            "se permite exactamente una sentencia SQL"
        )

    expression = statements[0]
    _reject_forbidden_nodes(expression)

    if isinstance(expression, exp.SetOperation):
        raise SQLValidationError(
            "no se permiten UNION, INTERSECT ni EXCEPT como consulta raíz"
        )
    if not isinstance(expression, exp.Select):
        raise SQLValidationError("solo se permite una consulta SELECT")

    if expression.args.get("into") is not None:
        raise UnauthorizedQueryError("no se permite SELECT INTO")

    _validate_sources(expression)
    _validate_functions(expression)

    return expression.sql(dialect="sqlite")
