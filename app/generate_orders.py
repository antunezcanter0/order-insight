"""Generador de pedidos de ejemplo en formato CSV.

Crea un fichero con pedidos sintéticos e introduce de forma deliberada
tres tipos de anomalías (identificadores duplicados, fechas mal formateadas
e importes ausentes) para que puedan tratarse en fases posteriores.

Uso:
    python -m app.generate_orders
    python -m app.generate_orders --rows 2000 --seed 7

Solo utiliza la biblioteca estándar de Python.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DEFAULT_ROWS = 1000
DEFAULT_SEED = 42
DEFAULT_OUTPUT = Path("data/orders_raw.csv")

FIELDNAMES = [
    "order_id",
    "order_date",
    "customer",
    "country",
    "product",
    "amount",
    "status",
]

DATE_START = date(2024, 1, 1)
DATE_END = date(2025, 12, 31)

# Cliente -> país, para que ambos campos sean coherentes entre sí.
CUSTOMERS: list[tuple[str, str]] = [
    ("Ana Torres", "España"),
    ("Luis Fernández", "España"),
    ("Marta Ruiz", "España"),
    ("Carlos Mendoza", "México"),
    ("Sofía Ramírez", "México"),
    ("Jorge Castillo", "México"),
    ("Valentina Gómez", "Colombia"),
    ("Andrés Salazar", "Colombia"),
    ("Camila Rojas", "Chile"),
    ("Diego Herrera", "Chile"),
    ("Lucía Ibáñez", "Argentina"),
    ("Mateo Acosta", "Argentina"),
    ("Rosa Quispe", "Perú"),
    ("Miguel Chávez", "Perú"),
    ("Emily Johnson", "Estados Unidos"),
    ("Daniel Smith", "Estados Unidos"),
    ("Hannah Miller", "Estados Unidos"),
    ("Lena Schmidt", "Alemania"),
    ("Jonas Weber", "Alemania"),
    ("Claire Dubois", "Francia"),
]

# Producto -> (precio mínimo, precio máximo) en euros.
PRODUCTS: list[tuple[str, float, float]] = [
    ("Laptop Pro 14", 900.0, 1600.0),
    ("Laptop Air 13", 700.0, 1200.0),
    ("Monitor 27\"", 180.0, 420.0),
    ("Teclado mecánico", 45.0, 150.0),
    ("Ratón inalámbrico", 15.0, 70.0),
    ("Auriculares Bluetooth", 20.0, 320.0),
    ("Altavoz portátil", 25.0, 180.0),
    ("Cámara web HD", 30.0, 140.0),
    ("Disco SSD 1 TB", 60.0, 190.0),
    ("Silla ergonómica", 120.0, 480.0),
    ("Escritorio ajustable", 220.0, 750.0),
    ("Tablet 10\"", 150.0, 600.0),
    ("Smartphone X", 300.0, 1100.0),
    ("Impresora láser", 100.0, 400.0),
    ("Router WiFi 6", 40.0, 220.0),
]

STATUSES: list[tuple[str, int]] = [
    ("delivered", 55),
    ("shipped", 20),
    ("pending", 15),
    ("cancelled", 6),
    ("returned", 4),
]

MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# Proporción de registros afectados por cada error intencionado.
ERROR_RATE = 0.02


@dataclass(frozen=True)
class GenerationResult:
    """Resumen de una generación de pedidos."""

    output_path: Path
    rows: int
    unique_order_ids: int
    duplicate_ids: int
    malformed_dates: int
    missing_amounts: int

    @property
    def total_anomalies(self) -> int:
        return self.duplicate_ids + self.malformed_dates + self.missing_amounts


def _random_date(rng: random.Random) -> date:
    days = (DATE_END - DATE_START).days
    return DATE_START + timedelta(days=rng.randrange(days + 1))


def _malformed_date(value: date, rng: random.Random) -> str:
    """Devuelve la misma fecha con uno de varios formatos incorrectos."""
    day, month, year = value.day, value.month, value.year
    variants = [
        f"{day:02d}/{month:02d}/{year}",
        f"{year}/{month:02d}/{day:02d}",
        f"{day:02d}-{month:02d}-{year}",
        f"{MONTH_NAMES[month - 1]} {day}, {year}",
    ]
    return rng.choice(variants)


def _build_order(rng: random.Random, number: int) -> dict[str, object]:
    customer, country = rng.choice(CUSTOMERS)
    product, price_min, price_max = rng.choice(PRODUCTS)
    status = rng.choices(
        [name for name, _ in STATUSES],
        weights=[weight for _, weight in STATUSES],
        k=1,
    )[0]
    return {
        "order_id": f"ORD-{number:06d}",
        "order_date": _random_date(rng),
        "customer": customer,
        "country": country,
        "product": product,
        "amount": round(rng.uniform(price_min, price_max), 2),
        "status": status,
    }


def _sample_indices(
    rng: random.Random,
    available: list[int],
    rows: int,
) -> list[int]:
    """Elige índices a corromper, como máximo el ERROR_RATE de las filas."""
    count = min(round(rows * ERROR_RATE), len(available))
    return rng.sample(available, count)


def generate_orders(
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
    output_path: Path | str = DEFAULT_OUTPUT,
) -> GenerationResult:
    """Genera ``rows`` pedidos, inyecta los errores y escribe el CSV."""
    if rows <= 0:
        raise ValueError("rows debe ser mayor que cero")

    rng = random.Random(seed)
    records = [_build_order(rng, number) for number in range(1, rows + 1)]

    # 1) Identificadores duplicados: se reutiliza el id de un pedido anterior.
    # Se procesan en orden ascendente para que el id original de cada fila
    # elegida desaparezca y cada error produzca un duplicado real.
    duplicated_indices = _sample_indices(rng, list(range(1, rows)), rows)
    for index in sorted(duplicated_indices):
        source = rng.randrange(0, index)
        records[index]["order_id"] = records[source]["order_id"]
    duplicated = set(duplicated_indices)

    # 2) Fechas con formato incorrecto.
    available = [index for index in range(rows) if index not in duplicated]
    malformed_indices = _sample_indices(rng, available, rows)
    for index in malformed_indices:
        current = records[index]["order_date"]
        assert isinstance(current, date)
        records[index]["order_date"] = _malformed_date(current, rng)
    malformed = set(malformed_indices)

    # 3) Importes ausentes.
    available = [
        index
        for index in range(rows)
        if index not in duplicated and index not in malformed
    ]
    missing_indices = _sample_indices(rng, available, rows)
    for index in missing_indices:
        records[index]["amount"] = ""

    for record in records:
        current = record["order_date"]
        if isinstance(current, date):
            record["order_date"] = current.isoformat()
        amount = record["amount"]
        if isinstance(amount, float):
            record["amount"] = f"{amount:.2f}"

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)

    unique_ids = len({record["order_id"] for record in records})
    return GenerationResult(
        output_path=destination,
        rows=rows,
        unique_order_ids=unique_ids,
        duplicate_ids=len(duplicated_indices),
        malformed_dates=len(malformed_indices),
        missing_amounts=len(missing_indices),
    )


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("se esperaba un número entero") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("debe ser mayor que cero")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Genera un CSV de pedidos de ejemplo con errores intencionados.",
    )
    parser.add_argument(
        "--rows",
        type=_positive_int,
        default=DEFAULT_ROWS,
        help=f"número de pedidos a generar (por defecto: {DEFAULT_ROWS})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"semilla aleatoria reproducible (por defecto: {DEFAULT_SEED})",
    )
    return parser


def print_summary(result: GenerationResult) -> None:
    print(f"CSV generado: {result.output_path}")
    print(f"Pedidos generados: {result.rows}")
    print("Errores introducidos:")
    print(f"  - order_id duplicados:    {result.duplicate_ids}")
    print(f"  - fechas mal formateadas: {result.malformed_dates}")
    print(f"  - importes ausentes:      {result.missing_amounts}")
    print(f"Total de anomalías: {result.total_anomalies}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = generate_orders(rows=args.rows, seed=args.seed)
    print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
