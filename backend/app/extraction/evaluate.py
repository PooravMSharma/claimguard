"""Compare extracted documents with the synthetic answers (record.json): python -m app.cli evaluate-extraction."""

from collections import Counter
from dataclasses import dataclass, field

from app.extraction.model import DocExtract
from app.extraction.values import norm_text

TRUE_TYPE = {
    "claim_form": "claim_form", "preauth": "preauth_form", "discharge_summary": "discharge_summary",
    "final_bill": "final_bill", "pharmacy_bill": "pharmacy_bill", "lab_report": "lab_report",
    "imaging_report": "imaging_report", "ot_note": "ot_notes", "implant_invoice": "implant_invoice",
    "implant_sticker": "implant_sticker", "nursing_chart": "nursing_chart", "mlc": "mlc_fir",
    "policy_ecard": "policy_copy", "id_proof": "id_proof",
}
TEXT = {"patient_name", "uhid", "rohini_id", "hospital_name", "policy_no", "member_id", "bill_no", "doctor_name",
        "doctor_reg_no", "preauth_no", "mlc_no", "police_station", "surgeon", "brand", "model", "serial",
        "id_last4", "test", "product_code", "diagnosis", "preauth_diagnosis", "history", "impression", "icd_code",
        "gender", "ward_type", "room_category"}
NUMBER = {"age", "claimed_amount", "bill_total", "estimate", "approved", "pharmacy_total", "sum_insured", "used_si"}
DATETIME = {"admission", "discharge", "report_date", "procedure_date"}
DATE = {"submitted", "policy_start", "policy_end", "inception"}
# Key fields for the PRD target (>= 95% clean, >= 85% scans)
KEY = {"patient_name", "policy_no", "rohini_id", "hospital_name", "doctor_reg_no", "admission", "discharge",
       "diagnosis", "claimed_amount", "bill_total", "bill_no", "preauth_diagnosis", "approved", "pharmacy_total",
       "sum_insured", "policy_start", "policy_end", "inception", "model", "procedure_date", "report_date", "test",
       "mlc_no", "uhid", "age", "gender", "icd_code"}


def _got(doc: DocExtract, name: str):
    v = doc.fields.get(name)
    if v is None:
        return None
    return v.normalized if v.normalized is not None else v.text


def same(name: str, want, got) -> bool:
    if got is None:
        return False
    if name in NUMBER:
        try:
            return abs(float(want) - float(got)) < 0.011
        except (TypeError, ValueError):
            return False
    if name in DATETIME:
        return str(got)[:16] == str(want)[:16]
    if name in DATE:
        return str(got)[:10] == str(want)[:10]
    if name == "procedures":
        got = [got] if isinstance(got, str) else (got or [])
        return sorted(norm_text(x) for x in want) == sorted(norm_text(str(x)) for x in got)
    if name == "impression":  # reports print the first sentence under "Impression"
        return norm_text(got) != "" and norm_text(want).startswith(norm_text(got))
    if name == "signed":
        return bool(got) == bool(want)
    if name == "sub_limits":
        if not isinstance(got, dict):
            return False
        try:
            return {k: float(v) for k, v in (want or {}).items()} == {k: float(v) for k, v in got.items()}
        except (TypeError, ValueError):
            return False
    return norm_text(want) == norm_text(got)


@dataclass
class Tally:
    fields: Counter = field(default_factory=Counter)        # name -> right
    totals: Counter = field(default_factory=Counter)        # name -> compared
    rows_right: int = 0
    rows_total: int = 0
    misses: list = field(default_factory=list)
    claims: int = 0
    not_extracted: int = 0

    def add(self, name: str, ok: bool, detail=None) -> None:
        self.totals[name] += 1
        if ok:
            self.fields[name] += 1
        elif len(self.misses) < 400:
            self.misses.append((name, detail))

    def rate(self, names: set[str] | None = None) -> float:
        names = names or set(self.totals)
        tot = sum(self.totals[n] for n in names)
        return sum(self.fields[n] for n in names) / tot if tot else 0.0


def _rows(tally: Tally, label: str, want_rows: list[dict], got_rows: list[dict], pairs: dict[str, str]) -> None:
    for i, want in enumerate(want_rows):
        got = got_rows[i] if i < len(got_rows) else {}
        ok = True
        for wkey, gkey in pairs.items():
            if wkey not in want:
                continue
            g = got.get(gkey)
            gval = None if g is None else (g.normalized if g.normalized is not None else g.text)
            if isinstance(want[wkey], (int, float)) and not isinstance(want[wkey], bool):
                good = gval is not None and same("bill_total", want[wkey], gval)
            else:
                good = gval is not None and norm_text(want[wkey]) == norm_text(gval)
            ok = ok and good
        tally.rows_total += 1
        tally.rows_right += ok
        if not ok and len(tally.misses) < 400:
            tally.misses.append((label, (want, {k: v.text for k, v in got.items()})))


def compare(docs: list[DocExtract], record: dict, tally: Tally) -> None:
    by_type: dict[str, list[DocExtract]] = {}
    for d in docs:
        by_type.setdefault(d.doc_type, []).append(d)
    seen: Counter = Counter()
    for truth in record["documents"]:
        t = TRUE_TYPE[truth["type"]]
        idx = seen[t]
        seen[t] += 1
        candidates = by_type.get(t, [])
        doc = candidates[idx] if idx < len(candidates) else None
        f = truth["fields"]
        for name, want in f.items():
            compared = TEXT | NUMBER | DATETIME | DATE | {"procedures", "signed", "sub_limits"}
            if name in compared and want not in (None, ""):
                if t == "discharge_summary" and name in ("doctor_name", "doctor_reg_no") and not f.get("signed"):
                    continue
                got = _got(doc, name) if doc else None
                tally.add(name, same(name, want, got), (t, want, got))
        if doc is None:
            continue
        if t == "final_bill":
            _rows(tally, "bill_line", f.get("lines", []), doc.tables.get("lines", []),
                  {"description": "description", "amount": "amount", "qty": "qty"})
        elif t == "pharmacy_bill":
            _rows(tally, "pharmacy_line", f.get("lines", []), doc.tables.get("lines", []),
                  {"drug": "drug", "mrp": "mrp", "rate": "rate", "qty": "qty"})
        elif t == "lab_report":
            _rows(tally, "lab_result", f.get("results", []), doc.tables.get("results", []),
                  {"parameter": "parameter", "value": "value"})
        elif t == "nursing_chart":
            _rows(tally, "nursing_day", f.get("days", []), doc.tables.get("days", []),
                  {"location": "location"})
        elif t in ("ot_notes", "implant_invoice"):
            _rows(tally, "implant", f.get("implants", []), doc.tables.get("implants", []), {"model": "model"})


def report(label: str, tally: Tally) -> str:
    worst = sorted(((n, tally.fields[n] / tally.totals[n]) for n in tally.totals if tally.fields[n] < tally.totals[n]),
                   key=lambda x: x[1])[:8]
    rows = f"{tally.rows_right / tally.rows_total:.1%}" if tally.rows_total else "n/a"
    return (f"{label}: key fields {tally.rate(KEY & set(tally.totals)):.1%} | all fields {tally.rate():.1%} | "
            f"table rows {rows}\n   weakest: " + ", ".join(f"{n} {r:.0%}" for n, r in worst))


def docs_from_db(rows) -> list[DocExtract]:
    """Rebuild DocExtract objects from saved ExtractedDocument rows (for scoring stored results)."""
    from app.extraction.model import Value

    def val(d: dict) -> Value:
        return Value(d.get("text", ""), d.get("normalized"), d.get("page_id"), d.get("bbox"),
                     d.get("confidence", 1.0), d.get("method", "rule"))

    docs = []
    for r in rows:
        data = r.data or {}
        doc = DocExtract(r.key, r.doc_type, r.page_ids, r.quality or "good",
                         {k: val(v) for k, v in data.get("fields", {}).items()},
                         {t: [{c: val(v) for c, v in row.items()} for row in rows_]
                          for t, rows_ in data.get("tables", {}).items()}, r.ai_used)
        docs.append(doc)
    return docs


def evaluate_db(db, folder) -> dict[str, Tally]:
    import json

    from sqlalchemy import select

    from app.models.claim import Claim
    from app.models.extraction import ExtractedDocument

    tallies = {"clean": Tally(), "scanned": Tally()}
    for claim in db.scalars(select(Claim).where(Claim.source == "synthetic", Claim.status == "ready_for_review")):
        base = claim.claim_number.removesuffix("-S")
        path = folder / "claims" / base / "record.json"
        if not path.exists():
            continue
        rows = db.scalars(select(ExtractedDocument).where(ExtractedDocument.claim_id == claim.id)
                          .order_by(ExtractedDocument.key)).all()
        tally = tallies["scanned" if claim.claim_number.endswith("-S") else "clean"]
        if not rows:  # processed before extraction existed; reprocess it to score it
            tally.not_extracted += 1
            continue
        order = {pid: i for i, pid in enumerate(p for r in rows for p in r.page_ids)}
        rows = sorted(rows, key=lambda r: min((order[p] for p in r.page_ids), default=0))
        tally.claims += 1
        compare(docs_from_db(rows), json.loads(path.read_text()), tally)
    return tallies
