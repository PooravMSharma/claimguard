"""Text clean-up shared by the matchers and importers."""

import re
from decimal import Decimal, InvalidOperation

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise(text: str | None) -> str:
    """Lower case, '&' -> 'and', everything else that isn't a letter or digit becomes one space."""
    if not text:
        return ""
    return _NON_ALNUM.sub(" ", text.lower().replace("&", " and ")).strip()


def header_key(text: object) -> str:
    """Column heading reduced to letters and digits: 'Rate (NABH)' -> 'ratenabh'."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def parse_money(value: object) -> Decimal | None:
    """'₹ 1,250.00', 'Rs. 1250', 1250 -> Decimal('1250.00'). Blank or '-' -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = re.sub(r"(?i)rs\.?|inr|₹|,|\s", "", str(value))
    if text in {"", "-", "--", "na", "NA", "N/A", "nil"}:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return amount.quantize(Decimal("0.01"))


def parse_int(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def parse_bool(value: object) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"y", "yes", "true", "1", "day care", "daycare"}:
        return True
    if text in {"n", "no", "false", "0"}:
        return False
    return None


def split_list(value: object) -> list[str]:
    """'a | b; c' -> ['a', 'b', 'c']"""
    return [part.strip() for part in re.split(r"[|;\n]", str(value or "")) if part.strip()]
