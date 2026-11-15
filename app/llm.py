"""Integración con el proveedor LLM para generar SQL."""

from __future__ import annotations

from typing import Protocol

from openai import OpenAI

MAX_SQL_LENGTH = 10_000

SYSTEM_PROMPT_TEMPLATE = """\
Eres un generador de SQL para SQLite. Devuelve únicamente una consulta \
SELECT, sin explicaciones, comentarios ni bloques Markdown.

Esquema disponible:

{schema}

Valores posibles de status: delivered, shipped, pending, cancelled, returned.

Reglas obligatorias:
- Devuelve exactamente una sentencia SELECT.
- Usa exclusivamente las tablas y vistas incluidas en el esquema.
- No uses INSERT, UPDATE, DELETE, DDL, PRAGMA, ATTACH ni funciones del sistema.
- No uses SELECT INTO.
- No uses UNION, INTERSECT ni EXCEPT como operador principal.
- Para filtrar por año o mes usa strftime sobre order_date.
- Usa alias claros para las columnas calculadas.
- Incluye LIMIT cuando la pregunta no requiera un agregado completo.
- Devuelve solo el SQL, sin marcas de código.
"""


class LLMError(Exception):
    """No se pudo obtener un SQL válido del proveedor LLM."""


class SQLGenerator(Protocol):
    def generate_sql(self, question: str, schema: str) -> str:
        """Genera una consulta SQL para ``question``."""


def build_system_prompt(schema: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(schema=schema.strip())


def clean_sql_response(content: str) -> str:
    """Elimina bloques Markdown envolventes de la respuesta del modelo."""
    text = content.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


class OpenAISQLGenerator:
    """Generador SQL basado en la API de chat completions de OpenAI."""

    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout = timeout

    def generate_sql(self, question: str, schema: str) -> str:
        if not self._api_key:
            raise LLMError(
                "no está configurada la variable OPENAI_API_KEY"
            )

        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
        )
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": build_system_prompt(schema),
                    },
                    {
                        "role": "user",
                        "content": f"Pregunta: {question}",
                    },
                ],
                temperature=0,
                max_tokens=500,
            )
        except Exception as exc:
            raise LLMError(
                "no se pudo obtener una respuesta del proveedor LLM"
            ) from exc
        finally:
            client.close()

        content = response.choices[0].message.content if response.choices else None
        if not content or not content.strip():
            raise LLMError("el proveedor LLM devolvió una respuesta vacía")

        cleaned = clean_sql_response(content)
        if len(cleaned) > MAX_SQL_LENGTH:
            raise LLMError("el SQL generado es demasiado largo")
        return cleaned
