"""Parsing helpers: dates, amounts, ages, ICD codes, doctors. Indian formats first (dd/mm/yyyy, 1,50,000.00)."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?:\s+(\d{1,2}):(\d{2}))?")
AMOUNT_RE = re.compile(r"-?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?|-?\d+(?:\.\d{1,2})?")
ICD_RE = re.compile(r"\b([A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?)\b")


def norm_text(value: object) -> str:
    """Lower case, letters and digits only: for comparing names and labels."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip(" :;,-|")
    return value or None


def parse_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    m = DATE_RE.search(text)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    hour = int(m.group(4)) if m.group(4) else 0
    minute = int(m.group(5)) if m.group(5) else 0
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def parse_date(text: str | None) -> date | None:
    dt = parse_datetime(text)
    return dt.date() if dt else None


def parse_amount(text: str | None) -> Decimal | None:
    """'Rs. 1,50,000.00' -> 150000.00. The last number in the text wins (amounts follow their labels)."""
    if text is None:
        return None
    cleaned = re.sub(r"(?i)rs\.?|inr|₹", " ", str(text))
    found = AMOUNT_RE.findall(cleaned.replace(" ", " "))
    if not found:
        return None
    digits = found[-1].replace(",", "")
    if re.match(r"0\d", digits):
        return None          # "00,000": the leading digit was lost on a scan ("§00,000" for "5,00,000")
    try:
        return Decimal(digits).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_number(text: str | None) -> Decimal | None:
    if text is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(text))
    if not m:
        return None
    try:
        return Decimal(m.group().replace(",", ""))
    except InvalidOperation:
        return None


def parse_age_sex(text: str | None) -> tuple[int | None, str | None]:
    """'37 Y / M', '37/M', '65 Years / Female' -> (37, 'M')."""
    if not text:
        return None, None
    age_m = re.search(r"\d{1,3}", text)
    sex_m = re.search(r"/\s*([MFO])\b|\b(male|female|other)\b", text, re.I)
    age = int(age_m.group()) if age_m else None
    sex = None
    if sex_m:
        sex = (sex_m.group(1) or sex_m.group(2) or "")[0].upper()
    return age, sex


def find_icd(text: str | None) -> str | None:
    if not text:
        return None
    explicit = re.search(r"ICD[- ]?10\s*[:)]?\s*([A-Z]\d{2}(?:\.[0-9A-Z]{1,4})?)", text, re.I)
    if explicit:
        return explicit.group(1).upper()
    found = ICD_RE.findall(text.upper())
    return found[-1] if found else None


def strip_icd(text: str | None) -> str | None:
    """'Dengue fever (ICD-10: A90)' -> 'Dengue fever'."""
    if not text:
        return None
    text = re.sub(r"\(\s*(?:ICD[- ]?10\s*:?\s*)?[A-Z]\d{2}(?:\.[0-9A-Z]{1,4})?\s*\)", "", text, flags=re.I)
    # a garbled code at the end, as scans give it: "(ICD-10: 809)", "(00-10: K80.20)"
    text = re.sub(r"\s*\((?=[^()]*\d)[^()]{1,24}\)\s*$", "", text)
    return clean(text)


def split_doctor(text: str | None) -> tuple[str | None, str | None]:
    """'Dr. Anjali Patel, MS (Orthopaedics)' -> ('Dr. Anjali Patel', 'MS (Orthopaedics)')."""
    text = clean(text)
    if not text:
        return None, None
    name, _, qual = text.partition(",")
    return fix_doctor_prefix(clean(name)), clean(qual)


DOCTOR_PREFIX = re.compile(r"^(?:Dr|Or|0r|D r)\s*[.,]?\s*(?=[A-Z])")


def fix_doctor_prefix(name: str | None) -> str | None:
    """OCR reads 'Dr.' as 'Or.', 'Dr,' or '0r'. A bare 'Dr'/'Or' with no name is no value at all."""
    if not name:
        return None
    if re.fullmatch(r"(?:Dr|Or|0r)\.?", name.strip()):
        return None
    return DOCTOR_PREFIX.sub("Dr. ", name.strip())


def rohini_digits(text: str | None) -> str | None:
    """Rohini IDs are 13 digits starting 89. Scans glue extra digits on ('ID' read as '10')."""
    digits = re.sub(r"\D", "", text or "")
    if len(digits) > 13 and (m := re.search(r"89\d{11}", digits)):
        return m.group()
    return digits or None
