"""Small helpers the checks share: money and date wording, name and code comparison."""

import re
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher

from app.extraction.model import DocExtract
from app.rules.context import ClaimContext, Evidence
from app.rules.reference import norm

DOC_LABEL = {
    "claim_form": "claim form", "preauth_form": "pre-authorisation", "discharge_summary": "discharge summary",
    "final_bill": "final bill", "pharmacy_bill": "pharmacy bill", "lab_report": "lab report",
    "imaging_report": "imaging report", "ot_notes": "OT notes", "implant_invoice": "implant invoice",
    "implant_sticker": "implant sticker", "nursing_chart": "nursing / medication chart", "mlc_fir": "MLC / FIR",
    "policy_copy": "policy copy / e-card", "id_proof": "photo ID proof",
}
TITLES = {"mr", "mrs", "ms", "miss", "smt", "shri", "sri", "kumari", "master", "baby", "dr", "late"}


def label(doc_type: str) -> str:
    return DOC_LABEL.get(doc_type, doc_type.replace("_", " "))


def rs(amount: Decimal | float | int | None) -> str:
    """Rs. 12,345 or Rs. 12,345.50, grouped the Indian way (1,23,456)."""
    if amount is None:
        return "Rs. -"
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    whole, frac = divmod(abs(value), 1)
    digits = str(int(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if len(head) > 2 else head
        digits = f"{head},{tail}"
    sign = "-" if value < 0 else ""
    return f"Rs. {sign}{digits}" + (f".{int(frac * 100):02d}" if frac else "")


def num_text(value: Decimal | float | int | None) -> str:
    """A quantity without trailing zeros: 2, 2.5."""
    if value is None:
        return "-"
    text = f"{Decimal(str(value)).normalize():f}"
    return text


def day(value: date | datetime | None) -> str:
    return value.strftime("%d %b %Y") if value else "-"


def moment(value: datetime | None) -> str:
    return value.strftime("%d %b %Y %H:%M") if value else "-"


def similar(a: object, b: object) -> float:
    x, y = norm(a), norm(b)
    if not x or not y:
        return 0.0
    return SequenceMatcher(None, x, y).ratio()


def name_words(name: str) -> list[str]:
    return [w for w in norm(name).split() if w not in TITLES]


def same_person(a: str, b: str, *, lenient: bool, word_similarity: float = 0.75) -> bool:
    """Two printed names refer to the same person.

    From a text layer the words must match (initials may stand for a word). From a scan, each word may differ
    by an OCR slip ('Rayesh Pillay' = 'Rajesh Pillai'), but a different first name is still a different person.
    """
    x, y = name_words(a), name_words(b)
    if not x or not y:
        return True
    if x == y:
        return True
    if len(x) != len(y):
        short, long_ = (x, y) if len(x) < len(y) else (y, x)
        # 'Rajesh Kumar Pillai' vs 'Rajesh Pillai': every word of the shorter name appears in order
        it = iter(long_)
        if all(any(w == v or (lenient and SequenceMatcher(None, w, v).ratio() >= word_similarity) for v in it)
               for w in short):
            return True
        return False
    for w, v in zip(x, y):
        if w == v or (len(w) == 1 and v.startswith(w)) or (len(v) == 1 and w.startswith(v)):
            continue
        if lenient and SequenceMatcher(None, w, v).ratio() >= word_similarity:
            continue
        return False
    return True


def code(text: object) -> str:
    """IDs compared on letters and digits only: 'UH-5979061' = 'uh5979061'."""
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def edits(a: str, b: str) -> int:
    """Levenshtein distance: characters to insert, delete or change to turn a into b."""
    if len(a) < len(b):
        a, b = b, a
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def same_code(a: object, b: object, *, lenient: bool) -> bool:
    """IDs match; from a scan one misread, missing or extra character is allowed ('UHS979061' = 'UH5979061')."""
    x, y = code(a), code(b)
    if not x or not y or x == y:
        return True
    return lenient and edits(x, y) <= 1


def doc_evidence(ctx: ClaimContext, doc: DocExtract, quote: str) -> Evidence:
    """Evidence pointing at a whole document (its first page) when there is no single value to point at."""
    return Evidence(doc.key, doc.page_ids[0] if doc.page_ids else None, None, quote, not ctx.scanned(doc))


def lenient(ctx: ClaimContext, *docs: DocExtract) -> bool:
    """Allow OCR slips when any of the documents compared was read from a scan."""
    return any(ctx.scanned(d) for d in docs)


def rohini(value) -> str | None:
    """A Rohini ID (13 digits) from a printed value, or None when the reading is not a clean ID."""
    digits = re.sub(r"\D", "", str(getattr(value, "normalized", None) or getattr(value, "text", value) or ""))
    if len(digits) == 13:
        return digits
    found = re.search(r"89\d{11}", digits)
    return found.group() if found else None
