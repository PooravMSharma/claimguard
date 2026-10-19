"""Render a synthetic claim into one PDF: one or more pages per document, in file order.

Returns the PDF bytes, the page range of every document, and the values each
document prints (the extraction target for Step 7).
"""

import io
import zlib
from dataclasses import dataclass
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.synthetic.case import Case, Document

STYLES = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=STYLES["Normal"], fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, leading=9, textColor=colors.HexColor("#444444"))
H_HOSP = ParagraphStyle("hosp", parent=STYLES["Title"], fontSize=15, leading=18, spaceAfter=0)
H_DOC = ParagraphStyle("doc", parent=STYLES["Heading2"], fontSize=12, alignment=TA_CENTER, spaceBefore=4,
                       spaceAfter=6)
H_SEC = ParagraphStyle("sec", parent=STYLES["Heading4"], fontSize=9.5, spaceBefore=6, spaceAfter=2)
ALT_HEAD = ParagraphStyle("alt", parent=STYLES["Title"], fontName="Courier-Bold", fontSize=13, leading=16)
ALT_BODY = ParagraphStyle("altbody", parent=BODY, fontName="Courier", fontSize=8.5)

GRID = TableStyle([
    ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9edf3")),
    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa4b2")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
])
KV = TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5),
                 ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2)])


def dt(value: datetime) -> str:
    return value.strftime("%d/%m/%Y %H:%M")


def d(value) -> str:
    return value.strftime("%d/%m/%Y")


def rs(value: float) -> str:
    return f"{value:,.2f}"


@dataclass
class Rendered:
    pdf: bytes
    pages: list[dict]          # [{type, title, first_page, last_page}]
    fields: list[dict]         # [{type, title, fields}]


class _Doc:
    """Builds one document's story and records the fields it prints."""

    def __init__(self, case: Case, doc: Document):
        self.case, self.doc, self.o = case, doc, doc.overrides
        self.story: list = []
        self.fields: dict = {}

    # identity as printed on THIS document (overrides plant inconsistencies)
    @property
    def patient_name(self) -> str:
        return self.o.get("patient_name", self.case.patient.name)

    @property
    def uhid(self) -> str:
        return self.o.get("uhid", self.case.patient.uhid)

    @property
    def rohini(self) -> str:
        return self.o.get("rohini_id", self.case.hospital.rohini_id)

    def letterhead(self, subtitle: str | None = None) -> None:
        h = self.case.hospital
        self.story += [
            Paragraph(h.name, H_HOSP),
            Paragraph(f"{h.address}, {h.city}, {h.state} &nbsp;|&nbsp; Ph: {h.phone}", SMALL),
            Paragraph(f"Rohini ID: {self.rohini} &nbsp;|&nbsp; HFR ID: {h.hfr_id}"
                      + (" &nbsp;|&nbsp; NABH Accredited" if h.nabh else ""), SMALL),
            Spacer(1, 4),
        ]
        if subtitle:
            self.story.append(Paragraph(subtitle, H_DOC))
        self.fields.update({"hospital_name": h.name, "rohini_id": self.rohini, "hospital_city": h.city})

    def kv(self, pairs: list[tuple[str, str]]) -> None:
        rows = []
        for i in range(0, len(pairs), 2):
            left = pairs[i]
            right = pairs[i + 1] if i + 1 < len(pairs) else ("", "")
            rows.append([left[0], Paragraph(str(left[1]), BODY), right[0], Paragraph(str(right[1]), BODY)])
        t = Table(rows, colWidths=[32 * mm, 58 * mm, 32 * mm, 58 * mm])
        t.setStyle(KV)
        self.story += [t, Spacer(1, 4)]

    def patient_block(self, extra: list[tuple[str, str]] | None = None) -> None:
        p, c = self.case.patient, self.case
        pairs = [("Patient", self.patient_name), ("UHID", self.uhid), ("Age / Sex", f"{p.age} Y / {p.gender}"),
                 ("IP No.", f"IP{zlib.crc32(c.claim_id.encode()) % 10**6:06d}"), ("Admitted", dt(c.admission)),
                 ("Discharged", dt(c.discharge))]
        self.kv(pairs + (extra or []))
        self.fields.update({"patient_name": self.patient_name, "uhid": self.uhid, "age": p.age, "gender": p.gender,
                            "admission": c.admission.isoformat(), "discharge": c.discharge.isoformat()})

    def section(self, title: str, text: str) -> None:
        self.story += [Paragraph(title, H_SEC), Paragraph(text, BODY)]

    def table(self, rows: list[list], widths: list[float], style: TableStyle = GRID) -> Table:
        t = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
        t.setStyle(style)
        self.story += [t, Spacer(1, 4)]
        return t

    def signature(self, name: str, role: str, reg: str | None = None) -> None:
        self.story += [Spacer(1, 18), Paragraph("_______________________", BODY),
                       Paragraph(f"<b>{name}</b>, {role}" + (f"<br/>Reg. No. {reg}" if reg else ""), BODY),
                       Paragraph("(Signature &amp; Stamp)", SMALL)]


# ---------------------------------------------------------------- documents

def claim_form(x: _Doc) -> None:
    c, p, pol, h = x.case, x.case.patient, x.case.policy, x.case.hospital
    x.story += [Paragraph(f"{pol.insurer}", H_HOSP), Paragraph(f"Claims handled by {pol.tpa}", SMALL),
                Paragraph("CLAIM FORM - HEALTH INSURANCE POLICY", H_DOC),
                Paragraph("PART A - To be filled by the insured", H_SEC)]
    x.kv([("Policy No.", pol.policy_no), ("Member ID", pol.member_id), ("Patient", x.patient_name),
          ("Age / Sex", f"{p.age} / {p.gender}"), ("Address", p.address), ("Mobile", p.phone),
          ("Claim type", "Cashless (final)" if c.stage == "final_cashless" else "Reimbursement"),
          ("Amount claimed (Rs.)", rs(c.claimed_amount))])
    x.story.append(Paragraph("PART B - To be filled by the hospital", H_SEC))
    x.kv([("Hospital", h.name), ("Rohini ID", x.rohini), ("Address", f"{h.address}, {h.city}"),
          ("Treating doctor", f"{c.doctor.name}, {c.doctor.qualification}"), ("Doctor Reg. No.", c.doctor.reg_no),
          ("Admission", dt(c.admission)), ("Discharge", dt(c.discharge)),
          ("Diagnosis (ICD-10)", f"{c.final_diagnosis} ({c.icd_code})"),
          ("Procedure", ", ".join(c.procedures) or "Medical management"), ("Bill No.", c.bill_no),
          ("Total bill (Rs.)", rs(c.bill_total)), ("Claimed (Rs.)", rs(c.claimed_amount))])
    x.story.append(Paragraph(f"I declare that the information given is true. Date: {d(c.claim_submitted)}", BODY))
    x.signature(p.name, "Insured / Claimant")
    x.fields.update({"policy_no": pol.policy_no, "member_id": pol.member_id, "patient_name": x.patient_name,
                     "claimed_amount": c.claimed_amount, "bill_total": c.bill_total, "bill_no": c.bill_no,
                     "doctor_name": c.doctor.name, "doctor_reg_no": c.doctor.reg_no, "diagnosis": c.final_diagnosis,
                     "icd_code": c.icd_code, "procedures": list(c.procedures),
                     "submitted": c.claim_submitted.isoformat(),
                     "rohini_id": x.rohini, "hospital_name": h.name, "stage": c.stage})


def preauth(x: _Doc) -> None:
    c, pa = x.case, x.case.preauth
    assert pa is not None, "preauth document on a claim without a pre-auth"
    x.letterhead("REQUEST FOR CASHLESS HOSPITALISATION (PRE-AUTHORISATION)")
    x.patient_block([("Policy No.", c.policy.policy_no), ("Requested on", dt(pa.requested_on))])
    x.kv([("Provisional diagnosis", pa.diagnosis), ("Proposed treatment", pa.procedure),
          ("Estimated cost (Rs.)", rs(pa.estimate)), ("Treating doctor", c.doctor.name)])
    x.story += [Spacer(1, 10), Paragraph(f"{c.policy.tpa} - AUTHORISATION LETTER", H_DOC)]
    x.kv([("Pre-auth No.", pa.number), ("Date", dt(pa.requested_on)),
          ("Approved amount (Rs.)", rs(pa.approved)), ("Diagnosis", pa.diagnosis)])
    x.story.append(Paragraph("Final approval subject to policy terms and submission of discharge documents.", SMALL))
    x.fields.update({"preauth_no": pa.number, "preauth_diagnosis": pa.diagnosis, "estimate": pa.estimate,
                     "approved": pa.approved})


def discharge_summary(x: _Doc) -> None:
    c = x.case
    x.letterhead("DISCHARGE SUMMARY")
    x.patient_block([("Ward", c.ward_type.replace("_", "-").title()), ("Consultant", c.doctor.name)])
    first = c.nursing[0]
    procs = "; ".join(c.discharge_procedures) if c.discharge_procedures else "None (medical management)"
    if c.ot and c.discharge_procedures:
        procs += f" on {d(c.ot.start)}"
    x.section("Final diagnosis", f"{c.final_diagnosis} (ICD-10: {c.icd_code})")
    x.section("Presenting complaints", c.complaints)
    x.section("Past history", c.history)
    x.section("Examination on admission", f"Temp {first.temp_f} F, Pulse {first.pulse}/min, BP {first.bp} mmHg, "
                                          f"RR {first.rr}/min, SpO2 {first.spo2}%")
    key = []
    for r in c.reports:
        if r.impression:
            key.append(f"{r.test} ({d(r.report_dt)}): {r.impression}")
        elif r.rows:
            values = ", ".join(f"{a} {b} {u}".strip() for a, b, u, _ in r.rows[:3])
            key.append(f"{r.test} ({d(r.report_dt)}): {values}")
    x.section("Investigations", "<br/>".join(key))
    x.section("Procedure(s)", procs)
    x.section("Course in hospital", c.course)
    x.section("Condition at discharge", "Stable. Vitals within normal limits.")
    x.section("Advice on discharge", c.advice)
    if c.signed_summary:
        x.signature(c.doctor.name, c.doctor.qualification, c.doctor.reg_no)
    else:
        x.story += [Spacer(1, 16), Paragraph("Prepared by: Duty Medical Officer", BODY)]
    x.fields.update({"diagnosis": c.final_diagnosis, "icd_code": c.icd_code, "procedures": list(c.discharge_procedures),
                     "doctor_name": c.doctor.name if c.signed_summary else None,
                     "doctor_reg_no": c.doctor.reg_no if c.signed_summary else None, "signed": c.signed_summary,
                     "history": c.history, "ward_type": c.ward_type})


def final_bill(x: _Doc) -> None:
    c = x.case
    x.letterhead("FINAL BILL - IN-PATIENT")
    x.patient_block([("Bill No.", c.bill_no), ("Bill date", d(c.discharge)),
                     ("Room category", c.ward_type.replace("_", "-").title()), ("Payer", c.policy.tpa)])
    rows: list[list] = [["#", "Date", "Category", "Description", "Qty", "Rate", "Amount"]]
    style = TableStyle(GRID.getCommands())
    for i, line in enumerate(c.bill, 1):
        rows.append([str(i), d(line.day), Paragraph(line.category, SMALL), Paragraph(line.description, BODY),
                     f"{line.qty:g}",
                     rs(line.rate), rs(line.amount)])
        if line.font:
            style.add("FONT", (6, i), (6, i), line.font, 9.5)
    rows.append(["", "", "", Paragraph("<b>GRAND TOTAL</b>", BODY), "", "", rs(c.bill_total)])
    style.add("FONT", (0, len(rows) - 1), (-1, len(rows) - 1), "Helvetica-Bold", 8.5)
    style.add("ALIGN", (4, 1), (-1, -1), "RIGHT")
    x.table(rows, [8, 18, 25, 61, 12, 24, 26], style)
    x.story.append(Paragraph(f"Net payable: Rs. {rs(c.bill_total)}. Discount: Rs. 0.00. GST: Not applicable "
                             "(healthcare services).", BODY))
    x.signature("Billing Executive", "Authorised Signatory")
    x.fields.update({"bill_no": c.bill_no, "bill_total": c.bill_total, "room_category": c.ward_type,
                     "lines": [{"date": line.day.isoformat(), "category": line.category,
                                "description": line.description, "qty": line.qty, "rate": line.rate,
                                "amount": line.amount} for line in c.bill]})


def pharmacy_bill(x: _Doc) -> None:
    c = x.case
    x.letterhead("IN-HOUSE PHARMACY - CREDIT BILL")
    x.patient_block([("Drug Lic. No.", f"RJ-{zlib.crc32(c.hospital.name.encode()) % 90000 + 10000}/20B")])
    rows: list[list] = [["#", "Item", "Batch", "Exp.", "Qty", "MRP", "Rate", "Amount"]]
    for i, line in enumerate(c.pharmacy, 1):
        rows.append([str(i), Paragraph(line.drug, BODY), line.batch, line.expiry, str(line.qty), rs(line.mrp),
                     rs(line.rate), rs(line.amount)])
    rows.append(["", Paragraph("<b>TOTAL</b>", BODY), "", "", "", "", "", rs(c.pharmacy_total)])
    style = TableStyle(GRID.getCommands())
    style.add("ALIGN", (4, 1), (-1, -1), "RIGHT")
    x.table(rows, [8, 58, 18, 15, 11, 20, 20, 24], style)
    x.signature("Pharmacist", "Registered Pharmacist")
    x.fields.update({"pharmacy_total": c.pharmacy_total,
                     "lines": [{"drug": p.drug, "batch": p.batch, "qty": p.qty, "mrp": p.mrp, "rate": p.rate,
                                "amount": p.amount} for p in c.pharmacy]})


def lab_report(x: _Doc) -> None:
    c = x.case
    r = c.reports[x.o["report_index"]]
    alt = r.layout == "alt"
    if alt:
        x.story += [Paragraph(r.lab_name.upper(), ALT_HEAD), Paragraph(c.hospital.name, ALT_BODY),
                    Paragraph(f"ROHINI {x.rohini}", ALT_BODY), Spacer(1, 6),
                    Paragraph(f"TEST REPORT: {r.test.upper()}", ALT_HEAD)]
        x.fields.update({"hospital_name": c.hospital.name, "rohini_id": x.rohini})
    else:
        x.letterhead(f"{r.lab_name}<br/>{r.test.upper()}")
    x.kv([("Patient", x.patient_name), ("UHID", x.uhid), ("Age / Sex", f"{c.patient.age} Y / {c.patient.gender}"),
          ("Ref. by", c.doctor.name), ("Collected", dt(r.report_dt)), ("Reported", dt(r.report_dt))])
    if r.kind == "lab":
        rows = [["Investigation", "Result", "Unit", "Reference range"]]
        rows += [[a, b, u, ref] for a, b, u, ref in r.rows]
        if alt:
            rows = [[row[0], row[3], row[1], row[2]] for row in rows]   # different column order
        x.table(rows, [65, 40, 30, 45])
        if r.impression:
            x.section("Impression", r.impression)
    x.signature(r.signatory, "Pathologist")
    x.fields.update({"patient_name": x.patient_name, "uhid": x.uhid, "test": r.test, "report_date":
                     r.report_dt.isoformat(), "results": [{"parameter": a, "value": b, "unit": u, "reference": ref}
                                                          for a, b, u, ref in r.rows], "layout": r.layout})


def imaging_report(x: _Doc) -> None:
    c = x.case
    r = c.reports[x.o["report_index"]]
    x.letterhead(f"Department of Radiology<br/>{r.test.upper()}")
    x.kv([("Patient", x.patient_name), ("UHID", x.uhid), ("Age / Sex", f"{c.patient.age} Y / {c.patient.gender}"),
          ("Ref. by", c.doctor.name), ("Study date", dt(r.report_dt)), ("Modality", r.test.split(" ")[0])])
    x.section("Findings", r.impression)
    x.section("Impression", r.impression.split(".")[0] + ".")
    x.signature(r.signatory, "Radiologist")
    x.fields.update({"patient_name": x.patient_name, "uhid": x.uhid, "test": r.test,
                     "report_date": r.report_dt.isoformat(), "impression": r.impression})


def ot_note(x: _Doc) -> None:
    c, ot = x.case, x.case.ot
    assert ot is not None, "OT note on a claim without surgery"
    x.letterhead("OPERATION NOTES AND ANAESTHESIA RECORD")
    x.patient_block()
    x.kv([("Date", d(ot.start)), ("Time", f"{ot.start:%H:%M} - {ot.end:%H:%M}"), ("Surgeon", ot.surgeon),
          ("Anaesthetist", ot.anaesthetist), ("Anaesthesia", ot.anaesthesia), ("Pre-op diagnosis", c.final_diagnosis)])
    x.section("Procedure(s) performed", "<br/>".join(ot.procedures))
    x.section("Operative findings", ot.findings)
    if ot.implants:
        rows = [["Implant", "Brand", "Model", "Serial / Lot"]]
        rows += [[i.type, i.brand, i.model, i.serial] for i in ot.implants]
        x.story.append(Paragraph("Implants used", H_SEC))
        x.table(rows, [45, 60, 35, 40])
    x.section("Post-op instructions", "Shift to ward. Monitor vitals. Analgesics as charted.")
    x.signature(ot.surgeon, "Operating Surgeon")
    x.fields.update({"procedures": list(ot.procedures), "procedure_date": ot.start.isoformat(),
                     "surgeon": ot.surgeon, "implants": [{"type": i.type, "brand": i.brand, "model": i.model,
                                                          "serial": i.serial} for i in ot.implants]})


def implant_invoice(x: _Doc) -> None:
    c = x.case
    x.story += [Paragraph("MedSupply Surgicals Pvt. Ltd. (fictional)", H_HOSP),
                Paragraph(f"Authorised distributor | GSTIN 08AAACM{zlib.crc32(c.claim_id.encode()) % 9000 + 1000}Q1ZX",
                          SMALL),
                Paragraph("TAX INVOICE", H_DOC)]
    x.kv([("Invoice No.", f"MS/{c.admission.year}/{c.claim_id[-4:]}"), ("Date", d(c.ot.start if c.ot else c.admission)),
          ("Bill to", c.hospital.name), ("Patient", x.patient_name)])
    rows = [["Item", "Model", "Serial", "Qty", "Amount"]]
    rows += [[Paragraph(f"{i.type} - {i.brand}", BODY), i.model, i.serial, "1", rs(i.price)] for i in c.implants]
    x.table(rows, [70, 35, 30, 12, 30])
    x.fields.update({"implants": [{"type": i.type, "model": i.model, "serial": i.serial, "price": i.price}
                                  for i in c.implants]})


def implant_sticker(x: _Doc) -> None:
    c = x.case
    imp = c.implants[0]
    model = c.sticker_model or imp.model
    x.letterhead("IMPLANT STICKER (affixed)")
    x.patient_block()
    rows = [["MANUFACTURER LABEL"], [Paragraph(f"<b>{imp.brand}</b><br/>{imp.type}<br/>REF {model}<br/>"
                                              f"LOT {imp.serial}<br/>STERILE R &nbsp; |&nbsp; ||| |||| | ||| || |||| |",
                                              BODY)]]
    t = Table(rows, colWidths=[90 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.2, colors.black), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f1f1"))]))
    x.story.append(t)
    x.fields.update({"brand": imp.brand, "model": model, "serial": imp.serial})


def nursing_chart(x: _Doc) -> None:
    c = x.case
    x.letterhead("NURSING / TPR CHART AND MEDICATION ADMINISTRATION RECORD")
    x.patient_block()
    rows: list[list] = [["Date", "Location", "Temp (F)", "Pulse", "BP", "RR", "SpO2", "Medicines administered"]]
    for n in c.nursing:
        rows.append([d(n.day), n.location, f"{n.temp_f}", str(n.pulse), n.bp, str(n.rr), f"{n.spo2}%",
                     Paragraph("; ".join(n.meds), SMALL)])
    x.table(rows, [18, 16, 14, 11, 16, 9, 12, 84])
    x.signature("Staff Nurse", "Nursing In-charge")
    x.fields.update({"days": [{"date": n.day.isoformat(), "location": n.location, "temp_f": n.temp_f,
                               "pulse": n.pulse, "bp": n.bp, "spo2": n.spo2, "meds": n.meds} for n in c.nursing]})


def mlc(x: _Doc) -> None:
    c = x.case
    assert c.mlc is not None, "MLC document on a non-accident claim"
    x.letterhead("MEDICO-LEGAL CASE (MLC) INTIMATION")
    x.patient_block()
    x.kv([("MLC No.", c.mlc["number"]), ("Date / time", dt(c.mlc["date"])), ("Police station", c.mlc["station"]),
          ("Nature", "Road traffic accident - fall from two-wheeler")])
    x.story.append(Paragraph("Intimation sent to the police station named above.", BODY))
    x.signature(c.doctor.name, "Casualty Medical Officer", c.doctor.reg_no)
    x.fields.update({"mlc_no": c.mlc["number"], "police_station": c.mlc["station"]})


def policy_ecard(x: _Doc) -> None:
    pol, p = x.case.policy, x.case.patient
    x.story += [Paragraph(pol.insurer, H_HOSP), Paragraph(f"TPA: {pol.tpa}", SMALL),
                Paragraph("HEALTH CARD AND POLICY SCHEDULE (EXTRACT)", H_DOC)]
    limits = ", ".join(f"{k.replace('_', ' ')}: Rs. {v:,}" for k, v in pol.sub_limits.items()) or "None"
    room = "Semi-private room" + (f", max {pol.room_rent_cap_pct:g}% of sum insured per day"
                                  if pol.room_rent_cap_pct else "")
    x.kv([("Policy No.", pol.policy_no), ("Product", f"{pol.product_name} ({pol.product_code})"),
          ("Member ID", pol.member_id), ("Insured member", x.patient_name), ("Age / Sex", f"{p.age} / {p.gender}"),
          ("Sum insured (Rs.)", f"{pol.sum_insured:,}"), ("Policy period", f"{d(pol.start)} to {d(pol.end)}"),
          ("First inception", d(pol.inception)), ("Room eligibility", room), ("Sub-limits", limits),
          ("Claims paid this year (Rs.)", f"{pol.used_si:,}"),
          ("Balance sum insured (Rs.)", f"{pol.sum_insured - pol.used_si:,}")])
    x.story.append(Paragraph("Standard IRDAI exclusions apply (Excl01-Excl18) unless covered by a benefit above.",
                             SMALL))
    x.fields.update({"policy_no": pol.policy_no, "product_code": pol.product_code, "sum_insured": pol.sum_insured,
                     "policy_start": pol.start.isoformat(), "policy_end": pol.end.isoformat(),
                     "inception": pol.inception.isoformat(), "sub_limits": dict(pol.sub_limits),
                     "used_si": pol.used_si, "room_rent_cap_pct": pol.room_rent_cap_pct})


def id_proof(x: _Doc) -> None:
    c, p = x.case, x.case.patient
    x.story += [Paragraph("GOVERNMENT PHOTO IDENTITY (masked copy)", H_DOC)]
    t = Table([["[PHOTO]", Paragraph(f"<b>{x.patient_name}</b><br/>Year of birth: {c.admission.year - p.age}<br/>"
                                     f"Gender: {'Male' if p.gender == 'M' else 'Female'}<br/>"
                                     f"ID: XXXX XXXX {p.id_last4}<br/>{p.address}", BODY)]],
              colWidths=[30 * mm, 100 * mm], rowHeights=[38 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, colors.black), ("GRID", (0, 0), (0, 0), 0.5, colors.grey),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "CENTER")]))
    x.story.append(t)
    x.fields.update({"patient_name": x.patient_name, "id_last4": p.id_last4})


RENDERERS = {
    "claim_form": claim_form, "preauth": preauth, "discharge_summary": discharge_summary, "final_bill": final_bill,
    "pharmacy_bill": pharmacy_bill, "lab_report": lab_report, "imaging_report": imaging_report, "ot_note": ot_note,
    "implant_invoice": implant_invoice, "implant_sticker": implant_sticker, "nursing_chart": nursing_chart,
    "mlc": mlc, "policy_ecard": policy_ecard, "id_proof": id_proof,
}


def render_document(case: Case, doc: Document) -> tuple[bytes, dict]:
    x = _Doc(case, doc)
    RENDERERS[doc.type](x)
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                      bottomMargin=12 * mm, title=doc.title).build(x.story)
    return buffer.getvalue(), x.fields


def render_case(case: Case) -> Rendered:
    writer = PdfWriter()
    pages, fields = [], []
    page_no = 1
    for doc in case.documents:
        pdf, printed = render_document(case, doc)
        reader = PdfReader(io.BytesIO(pdf))
        for page in reader.pages:
            writer.add_page(page)
        pages.append({"type": doc.type, "title": doc.title, "first_page": page_no,
                      "last_page": page_no + len(reader.pages) - 1})
        fields.append({"type": doc.type, "title": doc.title, "pages": [page_no, page_no + len(reader.pages) - 1],
                       "fields": printed})
        page_no += len(reader.pages)

    meta = case.pdf_meta or {}
    created = case.claim_submitted
    modified = meta.get("modified", created)
    writer.add_metadata({
        "/Title": f"Claim file {case.claim_id}",
        "/Producer": meta.get("producer", "MedSoft HIS 7.2 Print Server"),
        "/Creator": meta.get("creator", "MedSoft HIS 7.2"),
        "/CreationDate": created.strftime("D:%Y%m%d%H%M%S+05'30'"),
        "/ModDate": modified.strftime("D:%Y%m%d%H%M%S+05'30'"),
    })
    out = io.BytesIO()
    writer.write(out)
    return Rendered(out.getvalue(), pages, fields)