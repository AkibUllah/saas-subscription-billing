"""Money helpers. All amounts are integers in minor units (paisa, cents); no floats."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

BPS_DENOMINATOR = 10_000

_CURRENCY_SYMBOLS = {"BDT": "৳", "USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}


def round_half_up(value: Decimal | Fraction) -> int:
    if isinstance(value, Fraction):
        value = Decimal(value.numerator) / Decimal(value.denominator)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def percent_of(amount_minor: int, bps: int) -> int:
    """`bps` basis points of `amount_minor` (1500 bps = 15%), rounded half up."""
    if bps < 0:
        raise ValueError("bps must be >= 0")
    return round_half_up(Fraction(amount_minor * bps, BPS_DENOMINATOR))


def scale(amount_minor: int, fraction: Fraction) -> int:
    """Multiply an amount by an exact fraction and round half up."""
    return round_half_up(Fraction(amount_minor) * fraction)


def format_money(amount_minor: int, currency: str = "BDT") -> str:
    """`150000, "BDT"` -> `৳1,500.00`; negative amounts are rendered with a leading minus."""
    sign = "-" if amount_minor < 0 else ""
    major, minor = divmod(abs(amount_minor), 100)
    symbol = _CURRENCY_SYMBOLS.get(currency.upper(), f"{currency.upper()} ")
    return f"{sign}{symbol}{major:,}.{minor:02d}"
