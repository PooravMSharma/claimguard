"""ICD-10 code lookup.

The loaded code set is ICD-10-CM (public domain, US clinical modification).
Indian hospitals code in WHO ICD-10. The two agree at the 3-character category
level for almost all codes, so when a full code is not found we fall back to
its category and say so ("category" match), rather than calling it invalid.
"""

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.reference import Icd10Code

CODE_PATTERN = re.compile(r"^([A-Z])(\d)([0-9A-Z])\.?([0-9A-Z]{0,4})$")


def normalise_code(raw: str) -> str | None:
    """'a000' / 'A00.0' / ' a00 ' -> 'A00.0' / 'A00'. Not code-shaped -> None."""
    text = re.sub(r"[\s\-]", "", (raw or "").upper()).rstrip(".")
    match = CODE_PATTERN.match(text)
    if not match:
        return None
    letter, d1, d2, rest = match.groups()
    category = f"{letter}{d1}{d2}"
    return f"{category}.{rest}" if rest else category


@dataclass
class IcdLookup:
    input: str
    code: str | None
    valid_format: bool
    match_level: str  # "exact" | "category" | "none"
    description: str | None
    category: str | None
    category_description: str | None
    chapter: str | None
    is_billable: bool | None


def lookup(db: Session, raw: str) -> IcdLookup:
    code = normalise_code(raw)
    if code is None:
        return IcdLookup(raw, None, False, "none", None, None, None, None, None)
    category = code.split(".")[0]
    cat_row = db.get(Icd10Code, category)
    row = db.get(Icd10Code, code)
    if row:
        return IcdLookup(raw, code, True, "exact", row.description, category,
                         cat_row.description if cat_row else None, row.chapter, row.is_billable)
    if cat_row:
        return IcdLookup(raw, code, True, "category", None, category, cat_row.description, cat_row.chapter, None)
    return IcdLookup(raw, code, True, "none", None, category, None, None, None)
