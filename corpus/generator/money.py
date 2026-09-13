"""Money handling for the corpus generator.

Amounts are integers in paise throughout. Floats are never used for money: a
float rounding error in a generated statement would produce a balance break the
generator did not intend, and the completeness validator would then be testing an
accident rather than a designed defect.
"""

from __future__ import annotations


def rupees(paise: int) -> str:
    """Format paise using the Indian digit grouping (1,23,456.78)."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    digits = str(whole)

    if len(digits) <= 3:
        grouped = digits
    else:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join([*parts, tail])

    return f"{sign}{grouped}.{frac:02d}"


def from_rupees(amount: float) -> int:
    """Convert a rupee amount to paise. Used only for fixture setup."""
    return round(amount * 100)
