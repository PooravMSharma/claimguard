"""Everything a check needs about one claim, in one object.

Checks read the structured record from Step 8 (documents with fields and tables, every value carrying its
page and box) through this object, never through the database directly. Reviewer corrections are applied
before any check sees a value, so a corrected value always wins.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import cached_property

from app.extraction.model import DocExtract, Value
from app.extraction.store import bill_category
from app.extraction.values import parse_datetime
from app.models.enums import BillCategory
from app.rules.reference import PackageRef, PolicyRef, Reference, norm

SCAN_MIN_CONFIDENCE = 0.85

# Documents that count as "the claim form" etc. when a check needs one value and several documents print it.
ORDER = {
    "patient_name": ["claim_form", "policy_copy", "discharge_summary", "final_bill", "id_proof"],
    "admission": ["discharge_summary", "claim_form", "final_bill", "nursing_chart", "preauth_form"],
    "discharge": ["discharge_summary", "claim_form", "final_bill", "nursing_chart", "preauth_form"],
    "policy_no": ["claim_form", "policy_copy", "preauth_form"],
    "member_id": ["claim_form", "policy_copy"],
    "rohini_id": ["claim_form", "discharge_summary", "final_bill", "preauth_form"],
    "hospital_name": ["claim_form", "discharge_summary", "final_bill", "preauth_form"],
    "diagnosis": ["discharge_summary", "claim_form"],
    "icd_code": ["discharge_summary", "claim_form"],
    "doctor_name": ["discharge_summary", "claim_form"],
    "doctor_reg_no": ["discharge_summary", "claim_form"],
}


@dataclass
class Page:
    id: str
    doc_type: str
    source: str                       # text_layer | ocr | none
    quality: str | None = None
    image_hash: str | None = None
    file_id: str | None = None


@dataclass
class OriginalFile:
    id: str
    name: str
    content_type: str
    read: Callable[[], bytes]


@dataclass
class Evidence:
    """One value a finding rests on: where it is printed, and how far it can be trusted."""
    document: str
    page_id: str | None
    bbox: list[float] | None
    quote: str
    reliable: bool = True

    def as_json(self) -> dict:
        box = dict(zip(("x0", "y0", "x1", "y1"), self.bbox)) if self.bbox else None
        return {"document": self.document, "page_id": self.page_id, "bbox": box, "quote": self.quote[:300]}


@dataclass
class Line:
    """A final-bill line with the values it was read from."""
    doc: DocExtract
    no: int
    description: str
    category: BillCategory
    qty: Decimal | None
    rate: Decimal | None
    amount: Decimal | None
    day: date | None
    values: dict[str, Value]

    @property
    def main(self) -> Value:
        return self.values.get("description") or next(iter(self.values.values()))


@dataclass
class Facts:
    claim_number: str
    stage: str                        # final_cashless | reimbursement | ...
    source: str = "upload"


@dataclass
class ClaimContext:
    facts: Facts
    docs: list[DocExtract]
    pages: list[Page]
    ref: Reference
    files: list[OriginalFile] = field(default_factory=list)
    config: dict[str, dict] = field(default_factory=dict)       # rule id -> merged config

    # ------------------------------------------------------------ documents and values

    def docs_of(self, doc_type: str) -> list[DocExtract]:
        return [d for d in self.docs if d.doc_type == doc_type]

    def doc(self, doc_type: str) -> DocExtract | None:
        return next((d for d in self.docs if d.doc_type == doc_type), None)

    def has(self, doc_type: str) -> bool:
        return any(d.doc_type == doc_type for d in self.docs) or any(p.doc_type == doc_type for p in self.pages)

    @cached_property
    def _page_source(self) -> dict[str, str]:
        return {p.id: p.source for p in self.pages}

    def scanned(self, doc: DocExtract) -> bool:
        """Read by OCR rather than from a text layer: values can carry misreads."""
        return any(self._page_source.get(pid, "ocr") != "text_layer" for pid in doc.page_ids)

    def first(self, name: str, order: list[str] | None = None) -> tuple[DocExtract, Value] | None:
        """The value from the most trusted document that prints it."""
        types = order or ORDER.get(name)
        if types:
            for t in types:
                for d in self.docs_of(t):
                    if name in d.fields:
                        return d, d.fields[name]
        for d in self.docs:
            if name in d.fields:
                return d, d.fields[name]
        return None

    def every(self, name: str) -> list[tuple[DocExtract, Value]]:
        return [(d, d.fields[name]) for d in self.docs if name in d.fields]

    def usable(self, doc: DocExtract, value: Value | None) -> bool:
        """Clear enough to base a finding on.

        Text-layer values are exact; reviewer corrections and the AI reader's readings are taken as read. A value
        OCR'd from a scan is used only when the OCR was confident: on the synthetic scans, OCR confidence of at
        least 0.85 keeps about 70% of correct readings and drops about 90% of misreads. An unclear value makes
        a check "not evaluable" rather than raising a finding on a misread.
        """
        if value is None:
            return False
        if value.method == "reviewer" or not self.scanned(doc):
            return True
        if value.method == "ai":          # the AI reader marks what it could not read with confidence (0.6)
            return value.confidence >= 0.8
        return value.confidence >= SCAN_MIN_CONFIDENCE

    def ev(self, doc: DocExtract, value: Value, quote: str | None = None) -> Evidence:
        reliable = value.method == "reviewer" or (
            value.method != "ai" and value.confidence >= 0.8 and not self.scanned(doc))
        return Evidence(doc.key, value.page_id, value.bbox, quote or value.text, reliable)

    def config_of(self, rule_id: str) -> dict:
        return self.config.get(rule_id, {})

    # ------------------------------------------------------------ typed reads

    @staticmethod
    def num(value: Value | None) -> Decimal | None:
        if value is None:
            return None
        raw = value.normalized if value.normalized is not None else value.text
        if isinstance(raw, (int, float, Decimal)):
            return Decimal(str(raw)).quantize(Decimal("0.01"))
        text = re.sub(r"(?i)rs\.?|inr|₹|,|\s", "", str(raw))
        try:
            return Decimal(text).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None

    @staticmethod
    def when(value: Value | None) -> datetime | None:
        if value is None:
            return None
        raw = value.normalized if isinstance(value.normalized, str) else value.text
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return parse_datetime(value.text)

    def date_of(self, name: str, order: list[str] | None = None) -> tuple[DocExtract, Value, datetime] | None:
        hit = self.first(name, order)
        if hit is None:
            return None
        moment = self.when(hit[1])
        return (hit[0], hit[1], moment) if moment else None

    @cached_property
    def admission(self) -> datetime | None:
        hit = self.date_of("admission")
        return hit[2] if hit else None

    @cached_property
    def discharge(self) -> datetime | None:
        hit = self.date_of("discharge")
        return hit[2] if hit else None

    @property
    def stay_nights(self) -> int | None:
        if self.admission is None or self.discharge is None:
            return None
        return (self.discharge.date() - self.admission.date()).days

    @property
    def stay_days(self) -> int | None:
        """Days charged for: nights, but at least one."""
        nights = self.stay_nights
        return None if nights is None else max(1, nights)

    # ------------------------------------------------------------ bill, pharmacy, nursing

    @cached_property
    def bill_lines(self) -> list[Line]:
        out = []
        for doc in self.docs_of("final_bill"):
            for n, row in enumerate(doc.tables.get("lines", []), 1):
                desc = row.get("description")
                if desc is None:
                    continue
                cat = row["category"].text if "category" in row else None
                day = self.when(row.get("date"))
                out.append(Line(doc, n, desc.text, bill_category(cat, desc.text), self.num(row.get("qty")),
                                self.num(row.get("rate")), self.num(row.get("amount")), day.date() if day else None,
                                row))
        return out

    @cached_property
    def pharmacy_lines(self) -> list[tuple[DocExtract, dict[str, Value]]]:
        return [(d, row) for d in self.docs_of("pharmacy_bill") for row in d.tables.get("lines", []) if "drug" in row]

    @cached_property
    def nursing_days(self) -> list[tuple[DocExtract, dict[str, Value]]]:
        return [(d, row) for d in self.docs_of("nursing_chart") for row in d.tables.get("days", [])]

    @cached_property
    def charted_medicines(self) -> set[str]:
        meds: set[str] = set()
        for _, day in self.nursing_days:
            if "meds" in day:
                meds.update(norm(m) for m in re.split(r"[;,]", day["meds"].text) if norm(m))
        return meds

    # ------------------------------------------------------------ procedures, package, policy, hospital

    def procedures(self, doc_type: str) -> list[tuple[DocExtract, Value, str]]:
        """(document, value, procedure) for each procedure printed on documents of this type."""
        out = []
        for d in self.docs_of(doc_type):
            v = d.fields.get("procedures")
            if v is None:
                continue
            items = v.normalized if isinstance(v.normalized, list) else [v.text]
            out += [(d, v, str(p)) for p in items if str(p).strip()]
        return out

    @cached_property
    def billed_procedures(self) -> list[tuple[Line, str]]:
        """Procedures named on surgeon-fee lines: 'Surgeon fee - Laparoscopic cholecystectomy'."""
        out = []
        for line in self.bill_lines:
            m = re.match(r"(?i)\s*surgeon'?s? fee\s*[-–:]\s*(.+)", line.description)
            if m:
                out.append((line, m.group(1).strip()))
        return out

    @cached_property
    def package(self) -> PackageRef | None:
        """The package this claim falls under: a code printed on the bill, else the diagnosis / procedure."""
        packages = self.ref.packages
        if not packages:
            return None
        printed = " ".join(line.description for line in self.bill_lines).upper()
        for p in packages:
            if re.search(rf"\b{re.escape(p.code)}\b", printed):
                return p
        names = [p for _, _, p in self.procedures("ot_notes") + self.procedures("discharge_summary")]
        names += [p for _, p in self.billed_procedures]
        for p in packages:
            if any(norm(n) == norm(p.name) for n in names):
                return p
        icd = self.first("icd_code")
        category = re.sub(r"[^A-Z0-9]", "", icd[1].text.upper())[:3] if icd else ""
        diag = self.first("diagnosis")
        diagnosis = norm(diag[1].text) if diag else ""
        surgical = bool(names)
        candidates = []
        for p in packages:
            keys = [k for k in p.keywords]
            if category and any(k.upper() == category for k in keys):
                candidates.append(p)
            elif diagnosis and any(len(k) > 4 and norm(k) and norm(k) in diagnosis for k in keys):
                candidates.append(p)
        if len(candidates) > 1:  # a medical and a surgical package for the same condition
            by_kind = [p for p in candidates if ("surgeon_fee" in p.inclusions) == surgical]
            candidates = by_kind or candidates
        return candidates[0] if candidates else None

    @cached_property
    def policy_no(self) -> str | None:
        hit = self.first("policy_no")
        return hit[1].text if hit else None

    @cached_property
    def policy_record(self) -> PolicyRef | None:
        member = self.first("member_id")
        return self.ref.policy(self.policy_no, member[1].text if member else None)

    @cached_property
    def rohini_id(self) -> str | None:
        hit = self.first("rohini_id")
        if hit is None:
            return None
        return str(hit[1].normalized or re.sub(r"\D", "", hit[1].text)) or None

    @cached_property
    def city_tier(self) -> str | None:
        if self.ref.hospitals and self.rohini_id and self.rohini_id in self.ref.hospitals:
            tier = self.ref.hospitals[self.rohini_id].city_tier
            return tier if tier != "any" else None
        return None

    @cached_property
    def ward(self) -> str | None:
        """Ward category the patient stayed in, as billed."""
        for name in ("room_category", "ward_type"):
            hit = self.first(name, ["final_bill", "discharge_summary"])
            if hit:
                return str(hit[1].normalized or norm(hit[1].text).replace(" ", "_"))
        for line in self.bill_lines:
            if line.category == BillCategory.ROOM:
                return ward_of(line.description)
        return None


WARD_WORDS = [("semi_private", ("semi private", "semi-private", "twin sharing", "sharing")),
              ("private", ("private", "single room", "deluxe", "suite")),
              ("general", ("general", "ward"))]


def ward_of(text: str) -> str | None:
    low = text.lower()
    for ward, words in WARD_WORDS:
        if any(w in low for w in words):
            return ward
    return None
