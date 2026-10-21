"""Rules that read each document type: labelled fields, sections and tables -> DocExtract."""

import re

from app.extraction.layout import (
    Table,
    W,
    group_lines,
    labelled_fields,
    line_text,
    make_value,
    parse_table,
    sections,
    words_of,
)
from app.extraction.model import DocExtract, Value
from app.extraction.values import (
    find_icd,
    fix_doctor_prefix,
    parse_age_sex,
    parse_amount,
    parse_datetime,
    parse_number,
    rohini_digits,
    split_doctor,
    strip_icd,
)

# Printed label -> field name. Longest labels win, so "Bill date" beats "Date".
LABELS = {
    "Policy No.": "policy_no", "Policy Number": "policy_no", "Member ID": "member_id",
    "Patient": "patient_name", "Patient Name": "patient_name", "Insured member": "patient_name",
    "Age / Sex": "age_sex", "Age/Sex": "age_sex", "UHID": "uhid", "IP No.": "ip_no", "MRD No.": "uhid",
    "Address": "address", "Mobile": "mobile", "Claim type": "claim_type",
    "Amount claimed (Rs.)": "claimed_amount", "Claimed (Rs.)": "claimed_amount", "Claimed amount": "claimed_amount",
    "Hospital": "hospital_name", "Rohini ID": "rohini_id", "HFR ID": "hfr_id",
    "Treating doctor": "doctor", "Consultant": "doctor", "Doctor Reg. No.": "doctor_reg_no",
    "Admission": "admission", "Admitted": "admission", "Date of admission": "admission",
    "Discharge": "discharge", "Discharged": "discharge", "Date of discharge": "discharge",
    "Diagnosis (ICD-10)": "diagnosis", "Procedure": "procedures", "Bill No.": "bill_no", "Bill date": "bill_date",
    "Total bill (Rs.)": "bill_total", "Room category": "room_category", "Payer": "payer", "Ward": "ward_type",
    "Requested on": "requested_on", "Provisional diagnosis": "preauth_diagnosis",
    "Proposed treatment": "proposed_treatment", "Estimated cost (Rs.)": "estimate", "Pre-auth No.": "preauth_no",
    "Approved amount (Rs.)": "approved", "Diagnosis": "diagnosis_short", "Date": "date",
    "Drug Lic. No.": "drug_licence", "Ref. by": "ref_by", "Collected": "collected", "Reported": "report_date",
    "Study date": "report_date", "Modality": "modality", "Time": "time", "Surgeon": "surgeon",
    "Anaesthetist": "anaesthetist", "Anaesthesia": "anaesthesia", "Pre-op diagnosis": "preop_diagnosis",
    "Invoice No.": "invoice_no", "Bill to": "bill_to", "MLC No.": "mlc_no", "Date / time": "mlc_date",
    "Police station": "police_station", "Nature": "nature", "Product": "product",
    "Sum insured (Rs.)": "sum_insured", "Policy period": "policy_period", "First inception": "inception",
    "Room eligibility": "room_eligibility", "Sub-limits": "sub_limits",
    "Claims paid this year (Rs.)": "used_si", "Balance sum insured (Rs.)": "balance_si",
    "Rohini": "rohini_id", "Year of birth": "birth_year", "Gender": "gender_word", "ID": "masked_id",
    "REF": "model", "LOT": "serial",
}
DATE_LIKE = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}")
AMOUNT_LIKE = re.compile(r"\d")
VALID = {name: DATE_LIKE for name in ("admission", "discharge", "bill_date", "report_date", "collected",
                                      "requested_on", "mlc_date", "date", "inception", "policy_period")}
VALID.update({name: AMOUNT_LIKE for name in ("claimed_amount", "bill_total", "estimate", "approved", "sum_insured",
                                             "used_si", "balance_si", "age_sex", "rohini_id")})
MULTILINE = {"diagnosis", "procedures", "preauth_diagnosis", "proposed_treatment", "diagnosis_short",
             "preop_diagnosis", "room_eligibility", "nature", "hospital_name", "product", "sub_limits"}

DISCHARGE_HEADINGS = {
    "Final diagnosis": "diagnosis_section", "Presenting complaints": "complaints", "Past history": "history",
    "Examination on admission": "examination", "Investigations": "investigations_text",
    "Procedure(s)": "procedures_section", "Course in hospital": "course",
    "Condition at discharge": "condition", "Advice on discharge": "advice",
    "Procedure(s) performed": "procedures_section", "Operative findings": "findings",
    "Post-op instructions": "postop", "Findings": "findings", "Impression": "impression",
    "Implants used": "implants_heading",
}

BILL = Table({"no": ["#", "S.No", "Sr. No"], "date": ["Date"], "category": ["Category"],
              "description": ["Description", "Particulars"], "qty": ["Qty", "Quantity", "Units"],
              "rate": ["Rate", "Unit rate"], "amount": ["Amount"]},
             anchor="no", row_end="amount", stop=("grand total", "net payable", "total"))
PHARMACY = Table({"no": ["#", "S.No"], "drug": ["Item", "Drug", "Medicine"], "batch": ["Batch"],
                  "expiry": ["Exp.", "Expiry"], "qty": ["Qty"], "mrp": ["MRP"], "rate": ["Rate"],
                  "amount": ["Amount"]},
                 anchor="no", row_end="amount", stop=("total", "grand total"))
LAB = Table({"parameter": ["Investigation", "Test", "Parameter"], "value": ["Result", "Value"], "unit": ["Unit"],
             "reference": ["Reference range", "Normal range", "Biological reference"]},
            anchor="parameter", row_end="value", stop=("impression",))
NURSING = Table({"date": ["Date"], "location": ["Location", "Ward"], "temp": ["Temp (F)", "Temp"],
                 "pulse": ["Pulse"], "bp": ["BP"], "rr": ["RR"], "spo2": ["SpO2"],
                 "meds": ["Medicines administered", "Medicines"]},
                anchor="date", row_end="spo2")
OT_IMPLANTS = Table({"type": ["Implant"], "brand": ["Brand"], "model": ["Model"], "serial": ["Serial / Lot", "Serial"]},
                    anchor="type", row_end="serial", stop=("post-op",))
INVOICE = Table({"item": ["Item", "Description"], "model": ["Model"], "serial": ["Serial"], "qty": ["Qty"],
                 "amount": ["Amount"]}, anchor="item", row_end="amount")


# ---------------------------------------------------------------- helpers


def _dt(v: Value | None) -> Value | None:
    if v is None:
        return None
    parsed = parse_datetime(v.text)
    v.normalized = parsed.isoformat() if parsed else None
    return v if parsed else None


def _amount(v: Value | None) -> Value | None:
    if v is None:
        return None
    amt = parse_amount(v.text)
    v.normalized = float(amt) if amt is not None else None
    return v if amt is not None else None


def _set(doc: DocExtract, name: str, v: Value | None) -> None:
    if v is not None and name not in doc.fields:
        doc.fields[name] = v


SIGNATURE_LINE = re.compile(r"^(?:dr|or|0r)\b|\bMD\b|\b(?:radiolog|patholog|consultant)", re.I)


def _report_text(v: Value | None) -> Value | None:
    """A report section as one text, stopping at the reporting doctor's signature line."""
    if v is None:
        return None
    kept = []
    for line in v.normalized if isinstance(v.normalized, list) else [v.text]:
        if SIGNATURE_LINE.search(line):
            break
        kept.append(line.strip())
    text = " ".join(x for x in kept if x)
    return _derive(v, text, text) if text else None


def _derive(v: Value, text: str, normalized=None) -> Value:
    return Value(text, normalized, v.page_id, v.bbox, v.confidence, v.method)


def _common(doc: DocExtract, f: dict[str, Value]) -> None:
    """Identity and hospital fields that most documents print in their header."""
    for name in ("patient_name", "uhid", "rohini_id", "policy_no", "member_id", "bill_no", "ip_no", "preauth_no",
                 "mlc_no", "police_station", "surgeon", "anaesthetist", "anaesthesia", "product", "invoice_no",
                 "room_category", "claim_type", "hfr_id"):
        if name in f:
            _set(doc, name, f[name])
    if "age_sex" in f:
        age, sex = parse_age_sex(f["age_sex"].text)
        if age is not None:
            _set(doc, "age", _derive(f["age_sex"], str(age), age))
        if sex:
            _set(doc, "gender", _derive(f["age_sex"], sex, sex))
    for name in ("admission", "discharge"):
        _set(doc, name, _dt(f.get(name)))
    if "rohini_id" in f:
        doc.fields["rohini_id"].normalized = rohini_digits(f["rohini_id"].text)


NOT_A_NAME = re.compile(r"department|laboratory|radiology|pathology|\d|\||ph:", re.I)


def _hospital_from_letterhead(doc: DocExtract, lines: list[list[W]]) -> None:
    """The hospital's name is the letterhead line above the 'Rohini' line (skipping departments and addresses)."""
    if "hospital_name" in doc.fields or not lines:
        return
    for i, line in enumerate(lines[:5]):
        if "rohini" in line_text(line).lower():
            for cand in lines[:i]:
                if not NOT_A_NAME.search(line_text(cand)):
                    v = make_value(cand)
                    if v:
                        doc.fields["hospital_name"] = v
                    return
            return


def _doctor(doc: DocExtract, v: Value | None) -> None:
    if v is None:
        return
    name, qual = split_doctor(v.text)
    if name:
        doc.fields.setdefault("doctor_name", _derive(v, name, name))
    if qual:
        doc.fields.setdefault("doctor_qualification", _derive(v, qual, qual))


def _diagnosis(doc: DocExtract, v: Value | None, name: str = "diagnosis") -> None:
    if v is None:
        return
    icd = find_icd(v.text)
    text = strip_icd(v.text) or v.text
    doc.fields.setdefault(name, _derive(v, text, text))
    if icd:
        doc.fields.setdefault("icd_code", _derive(v, icd, icd))


def _signature(doc: DocExtract, lines: list[list[W]]) -> None:
    """Signed = a signature block naming a doctor with a registration number."""
    tail = lines[-6:]
    text = " ".join(line_text(line) for line in tail).lower()
    reg = re.search(r"reg\.?\s*no\.?\s*[:\-]?\s*([A-Z]{2,6}/\d{4}/\d{3,6}|[A-Z0-9/-]{6,})", " ".join(
        line_text(line) for line in tail), re.I)
    signed = bool(reg) and ("signature" in text or "stamp" in text)
    words = [w for line in tail for w in line]
    anchor = make_value(words[-6:]) if words else None
    if anchor:
        doc.fields["signed"] = _derive(anchor, "yes" if signed else "no", signed)
    if reg:
        for line in tail:
            if reg.group(1) in line_text(line):
                v = make_value([w for w in line if reg.group(1) in w.text] or line)
                if v:
                    doc.fields.setdefault("doctor_reg_no", _derive(v, reg.group(1), reg.group(1)))
                    sig_line = tail[tail.index(line) - 1] if tail.index(line) else None
                    if sig_line:
                        sv = make_value(sig_line)
                        if sv:
                            _doctor(doc, sv)


def _table_rows(rows: list[dict[str, Value]], numeric: tuple[str, ...] = (), dates: tuple[str, ...] = ()
                ) -> list[dict[str, Value]]:
    for row in rows:
        for col in numeric:
            if col in row:
                num = parse_number(row[col].text)
                row[col].normalized = float(num) if num is not None else None
        for col in dates:
            if col in row:
                dt = parse_datetime(row[col].text)
                row[col].normalized = dt.date().isoformat() if dt else None
    return rows


# ---------------------------------------------------------------- per document type


def extract_document(doc: DocExtract, lines: list[list[W]]) -> DocExtract:
    f = labelled_fields(lines, LABELS, MULTILINE, VALID)
    _common(doc, f)
    _hospital_from_letterhead(doc, lines)
    t = doc.doc_type
    if t == "claim_form":
        _set(doc, "hospital_name", f.get("hospital_name"))
        _doctor(doc, f.get("doctor"))
        _set(doc, "doctor_reg_no", f.get("doctor_reg_no"))
        _diagnosis(doc, f.get("diagnosis"))
        _set(doc, "claimed_amount", _amount(f.get("claimed_amount")))
        _set(doc, "bill_total", _amount(f.get("bill_total")))
        if "procedures" in f:
            p = f["procedures"]
            # several procedures are listed "A, B"; a comma before a lower-case word is part of a name ("..., right")
            items = [x.strip() for x in re.split(r",\s+(?=[A-Z])", p.text) if x.strip()]
            doc.fields["procedures"] = _derive(p, p.text, [] if "medical management" in p.text.lower() else items)
        decl = next((ln for ln in lines if "declare" in line_text(ln).lower()), None)
        if decl:
            dv = make_value(decl)
            if dv and (d := parse_datetime(dv.text)):
                doc.fields["submitted"] = _derive(dv, d.date().isoformat(), d.date().isoformat())
    elif t == "preauth_form":
        if "preauth_diagnosis" in f:
            doc.fields["preauth_diagnosis"] = f["preauth_diagnosis"]
        _set(doc, "proposed_treatment", f.get("proposed_treatment"))
        _set(doc, "estimate", _amount(f.get("estimate")))
        _set(doc, "approved", _amount(f.get("approved")))
        _set(doc, "requested_on", _dt(f.get("requested_on")))
    elif t == "discharge_summary":
        s = sections(lines, DISCHARGE_HEADINGS)
        _diagnosis(doc, s.get("diagnosis_section"))
        for name in ("history", "complaints", "course", "advice", "examination", "investigations_text"):
            _set(doc, name, s.get(name))
        if "procedures_section" in s:
            p = s["procedures_section"]
            text = re.sub(r"\s+on\s+\d{1,2}/\d{1,2}/\d{2,4}$", "", p.text)
            items = [] if text.lower().startswith("none") else [x.strip() for x in text.split(";") if x.strip()]
            doc.fields["procedures"] = _derive(p, text, items)
        if "ward_type" in f:
            w = f["ward_type"]
            doc.fields["ward_type"] = _derive(w, w.text, ward_code(w.text))
        _doctor(doc, f.get("doctor"))
        _signature(doc, lines)
    elif t == "final_bill":
        rows = _table_rows(parse_table(lines, BILL), numeric=("qty", "rate", "amount"), dates=("date",))
        doc.tables["lines"] = rows
        total_line = next((ln for ln in lines if line_text(ln).lower().startswith("grand total")
                           or "grand total" in line_text(ln).lower()), None)
        if total_line:
            _set(doc, "bill_total", _amount(make_value(total_line)))
        _set(doc, "bill_date", _dt(f.get("bill_date")))
        if "room_category" in f:
            r = f["room_category"]
            doc.fields["room_category"] = _derive(r, r.text, re.sub(r"[^a-z]+", "_", r.text.lower()).strip("_"))
    elif t == "pharmacy_bill":
        doc.tables["lines"] = _table_rows(parse_table(lines, PHARMACY), numeric=("qty", "mrp", "rate", "amount"))
        total_line = next((ln for ln in lines
                           if re.sub(r"[^a-z]", "", line_text(ln).lower()).startswith("total")), None)
        if total_line:
            _set(doc, "pharmacy_total", _amount(make_value(total_line)))
    elif t in ("lab_report", "imaging_report"):
        title = next((ln for ln in lines[:8] if line_text(ln).isupper() and len(line_text(ln)) > 2
                      and not re.search(r"ROHINI|DEPARTMENT|LABORATORY MEDICINE", line_text(ln))), None)
        if title:
            tv = make_value(title)
            if tv:
                name = re.sub(r"^(TEST REPORT|REPORT)\s*:\s*", "", tv.text, flags=re.I)
                doc.fields["test"] = _derive(tv, name, name.lower())
        rd = f.get("report_date") or f.get("collected")
        _set(doc, "report_date", _dt(rd))
        s = sections(lines, {"Findings": "findings", "Impression": "impression"})
        _set(doc, "impression", _report_text(s.get("impression")))
        if t == "lab_report":
            doc.tables["results"] = parse_table(lines, LAB)
    elif t == "ot_notes":
        if "date" in f and (d := parse_datetime(f["date"].text)):
            time_v = f.get("time")
            if time_v and (m := re.match(r"\s*(\d{1,2}):(\d{2})", time_v.text)):
                d = d.replace(hour=int(m.group(1)), minute=int(m.group(2)))
            doc.fields["procedure_date"] = _derive(f["date"], d.isoformat(), d.isoformat())
        s = sections(lines, DISCHARGE_HEADINGS)
        if "procedures_section" in s:
            p = s["procedures_section"]
            section_lines = p.normalized if isinstance(p.normalized, list) else [p.text]
            items = [str(x).strip() for x in section_lines if str(x).strip()]   # one procedure per line
            doc.fields["procedures"] = _derive(p, p.text, items)
        _set(doc, "findings", s.get("findings"))
        doc.tables["implants"] = parse_table(lines, OT_IMPLANTS)
    elif t == "implant_invoice":
        doc.tables["implants"] = _table_rows(parse_table(lines, INVOICE), numeric=("qty", "amount"))
    elif t == "implant_sticker":
        if "model" in f:
            doc.fields["model"] = f["model"]
        if "serial" in f:
            doc.fields["serial"] = f["serial"]
        label = next((i for i, ln in enumerate(lines) if "manufacturer label" in line_text(ln).lower()), None)
        if label is not None and label + 1 < len(lines):
            b = make_value(lines[label + 1])
            if b:
                doc.fields["brand"] = b
    elif t == "nursing_chart":
        doc.tables["days"] = _table_rows(parse_table(lines, NURSING), numeric=("temp", "pulse", "rr", "spo2"),
                                         dates=("date",))
    elif t == "mlc_fir":
        _set(doc, "mlc_date", _dt(f.get("mlc_date")))
    elif t == "policy_copy":
        _set(doc, "sum_insured", _amount(f.get("sum_insured")))
        _set(doc, "used_si", _amount(f.get("used_si")))
        _set(doc, "balance_si", _amount(f.get("balance_si")))
        if "policy_period" in f:
            p = f["policy_period"]
            found = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", p.text)
            if len(found) == 2:
                start, end = parse_datetime(found[0]), parse_datetime(found[1])
                if start and end:
                    doc.fields["policy_start"] = _derive(p, found[0], start.date().isoformat())
                    doc.fields["policy_end"] = _derive(p, found[1], end.date().isoformat())
        if "inception" in f and (d := parse_datetime(f["inception"].text)):
            doc.fields["inception"] = _derive(f["inception"], f["inception"].text, d.date().isoformat())
        if "room_eligibility" in f:
            r = f["room_eligibility"]
            doc.fields["room_eligibility"] = _derive(r, r.text, parse_room_eligibility(r.text))
        if "sub_limits" in f:
            s = f["sub_limits"]
            doc.fields["sub_limits"] = _derive(s, s.text, parse_sub_limits(s.text))
        if "product" in f and (m := re.search(r"\(([A-Z0-9-]+)\)", f["product"].text)):
            doc.fields["product_code"] = _derive(f["product"], m.group(1), m.group(1))
    elif t == "id_proof":
        name_line = next((ln for ln in lines if "photo" not in line_text(ln).lower() and "[" not in line_text(ln)
                          and "identity" not in line_text(ln).lower() and len(ln) <= 4
                          and all(w.text[:1].isupper() for w in ln)), None)
        if name_line:
            nv = make_value(name_line)
            if nv:
                doc.fields.setdefault("patient_name", nv)
        if "masked_id" in f and (m := re.search(r"(\d{4})\s*$", f["masked_id"].text)):
            doc.fields["id_last4"] = _derive(f["masked_id"], m.group(1), m.group(1))
        if "birth_year" in f and (m := re.search(r"\d{4}", f["birth_year"].text)):
            doc.fields["birth_year"] = _derive(f["birth_year"], m.group(), int(m.group()))
        if "gender_word" in f:
            g = f["gender_word"].text[:1].upper()
            doc.fields["gender"] = _derive(f["gender_word"], g, g)
    return tidy(doc)


def build_documents(pages: list[dict]) -> list[DocExtract]:
    """Group consecutive pages of the same type (within one file) into documents and read each.

    pages: [{id, file_id, doc_type, words, quality}] in file order.
    """
    groups: list[list[dict]] = []
    for p in pages:
        same = groups and groups[-1][-1]["file_id"] == p["file_id"] and groups[-1][-1]["doc_type"] == p["doc_type"]
        # separate lab/imaging reports are separate documents even when consecutive
        if same and p["doc_type"] not in ("lab_report", "imaging_report", "implant_sticker"):
            groups[-1].append(p)
        else:
            groups.append([p])
    counts: dict[str, int] = {}
    order = {"good": 0, "fair": 1, "poor": 2, "unreadable": 3, "blank": 4}
    docs = []
    for group in groups:
        t = group[0]["doc_type"]
        counts[t] = counts.get(t, 0) + 1
        key = t if counts[t] == 1 else f"{t}:{counts[t]}"
        worst = max((p.get("quality") or "good" for p in group), key=lambda q: order.get(q, 0))
        doc = DocExtract(key, t, [p["id"] for p in group], worst)
        words: list[W] = []
        for n, p in enumerate(group):
            words += words_of(p["id"], n, p["words"])
        extract_document(doc, group_lines(words))
        docs.append(doc)
    return docs


# ---------------------------------------------------------------- normalising values from any source (AI, reviewer)

DATETIME_FIELDS = {"admission", "discharge", "report_date", "procedure_date", "requested_on", "mlc_date"}
DATE_FIELDS = {"submitted", "policy_start", "policy_end", "inception", "bill_date"}
AMOUNT_FIELDS = {"claimed_amount", "bill_total", "estimate", "approved", "pharmacy_total", "sum_insured", "used_si",
                 "balance_si"}


WARDS = ["general", "semi_private", "private", "single_private", "twin_sharing", "deluxe", "suite", "icu", "iccu",
         "nicu", "hdu", "day_care"]


def ward_code(text: str) -> str:
    """'Semi-private' -> 'semi_private'. Snaps a small OCR slip ('sem_private') to the known ward only when it is
    clearly closest; anything doubtful stays as read, because the ward decides room-rent limits."""
    from difflib import SequenceMatcher

    code = re.sub(r"[^a-z]+", "_", text.lower()).strip("_")
    if code in WARDS:
        return code
    scored = sorted(((SequenceMatcher(None, code, w).ratio(), w) for w in WARDS), reverse=True)
    (best, ward), (second, _) = scored[0], scored[1]
    return ward if best >= 0.8 and best - second >= 0.1 else code


def tidy(doc: DocExtract) -> DocExtract:
    """Final clean-up of values whoever read them (rules or AI): OCR slips with one right answer."""
    for name in ("doctor_name", "surgeon", "anaesthetist"):
        v = doc.fields.get(name)
        if v is not None:
            fixed = fix_doctor_prefix(v.text)
            if fixed is None:
                del doc.fields[name]
            elif fixed != v.text:
                doc.fields[name] = _derive(v, fixed, fixed)
    for name in ("diagnosis", "preauth_diagnosis"):
        v = doc.fields.get(name)
        if v is not None and isinstance(v.normalized, str):
            v.normalized = strip_icd(v.normalized) or v.normalized
    return doc


def parse_room_eligibility(text: str) -> dict:
    """'Single private room, up to 1% of sum insured per day' -> {"text": ..., "cap_pct": 1.0}."""
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    return {"text": text, "cap_pct": float(pct.group(1)) if pct else None}


def parse_sub_limits(text: str) -> dict[str, float]:
    """'Cataract: Rs. 40,000; Knee replacement: Rs 1,50,000' -> {"cataract": 40000.0, "knee_replacement": ...}."""
    limits = {}
    for name, amt in re.findall(r"([a-z][a-z ]+?):\s*Rs\.?\s*([\d,]+)", text, re.I):
        digits = amt.replace(",", "")
        if digits:                       # a scan can leave just "," where the amount was
            limits[re.sub(r"\s+", "_", name.strip().lower())] = float(digits)
    return limits


def normalize(name: str, text: str | None):
    """The normalized form of a printed value, the same way the rules produce it."""
    if text is None or str(text).strip() == "":
        return None
    text = str(text).strip()
    if name in DATETIME_FIELDS:
        d = parse_datetime(text)
        return d.isoformat() if d else None
    if name in DATE_FIELDS:
        d = parse_datetime(text)
        return d.date().isoformat() if d else None
    if name in AMOUNT_FIELDS:
        a = parse_amount(text)
        return float(a) if a is not None else None
    if name == "age":
        n = parse_number(text)
        return int(n) if n is not None else None
    if name == "gender":
        return text[:1].upper() if text[:1].upper() in "MFO" else None
    if name == "rohini_id":
        return rohini_digits(text)
    if name == "icd_code":
        return find_icd(text) or text.upper()
    if name in ("diagnosis", "preauth_diagnosis"):
        return strip_icd(text) or text
    if name == "test":
        return text.lower()
    if name in ("ward_type", "room_category"):
        return ward_code(text)
    if name in ("doctor_name", "surgeon", "anaesthetist"):
        return fix_doctor_prefix(text)
    if name == "impression":
        return text
    if name == "signed":
        return text.lower() in ("yes", "true", "signed", "1")
    if name == "procedures":
        return [x.strip() for x in re.split(r"\s*;\s*", text) if x.strip()]
    if name == "room_eligibility":
        return parse_room_eligibility(text)
    if name == "sub_limits":
        return parse_sub_limits(text)
    if name == "birth_year":
        m = re.search(r"\d{4}", text)
        return int(m.group()) if m else None
    return text


def normalize_row(table: str, row: dict[str, Value]) -> dict[str, Value]:
    numeric = {"lines": ("qty", "rate", "amount", "mrp"), "days": ("temp", "pulse", "rr", "spo2"),
               "implants": ("qty", "amount", "price")}.get(table, ())
    for col, v in row.items():
        if col in numeric:
            n = parse_number(v.text)
            v.normalized = float(n) if n is not None else None
        elif col == "date":
            d = parse_datetime(v.text)
            v.normalized = d.date().isoformat() if d else None
    return row