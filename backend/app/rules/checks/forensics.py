"""F-layer (rule part): file metadata and pages used twice. Visual tampering checks (F-02, F-04) are AI."""

import io
import re
from collections import Counter
from datetime import datetime, timedelta

from app.rules.checks.common import (
    day,
    doc_evidence,
    label,
    same_code,
    same_person,
    similar,
)
from app.rules.context import ClaimContext, Evidence
from app.rules.engine import Flag, Result, check, flagged, not_applicable, not_evaluable
from app.rules.imagehash import distance

PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?")


def _pdf_date(raw: object) -> datetime | None:
    m = PDF_DATE.search(str(raw or ""))
    if not m:
        return None
    parts = [int(x) if x else 0 for x in m.groups()]
    try:
        return datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])
    except ValueError:
        return None


def _metadata(name: str, content_type: str, data: bytes) -> dict:
    """Producer / creator / dates of a PDF, or the Software tag of an image."""
    if content_type == "application/pdf" or name.lower().endswith(".pdf"):
        from pypdf import PdfReader

        try:
            info = PdfReader(io.BytesIO(data)).metadata or {}
        except Exception:
            return {}
        return {"software": " / ".join(str(info.get(k)) for k in ("/Producer", "/Creator") if info.get(k)),
                "modified": _pdf_date(info.get("/ModDate")), "created": _pdf_date(info.get("/CreationDate"))}
    try:
        from PIL import Image

        exif = Image.open(io.BytesIO(data)).getexif()
        return {"software": str(exif.get(305) or ""), "modified": None, "created": None}
    except Exception:
        return {}


@check("F-01")
def file_metadata(ctx: ClaimContext) -> Result:
    if not ctx.files:
        return not_evaluable("Original files not available.")
    cfg = ctx.config_of("F-01")
    editors = [s.lower() for s in cfg.get("editing_software", [])]
    submitted = ctx.date_of("submitted", ["claim_form"])
    late_after = submitted[2] + timedelta(hours=cfg.get("grace_hours", 24)) if submitted else None
    flags = []
    for f in ctx.files:
        meta = _metadata(f.name, f.content_type, f.read())
        if not meta:
            continue
        software = meta.get("software") or ""
        tool = next((e for e in editors if e in software.lower()), None)
        modified: datetime | None = meta.get("modified")
        created: datetime | None = meta.get("created")
        # A file saved again well after it was made, and after the claim was submitted. (A file simply created
        # late is normal: insurers and TPAs scan paper claims after they arrive.)
        reworked = bool(modified and created and modified - created > timedelta(hours=cfg.get("grace_hours", 24)))
        late = bool(late_after and modified and modified > late_after and reworked)
        if not tool and not late:
            continue
        reasons = []
        if tool:
            reasons.append(f"made or saved with image-editing software ({software})")
        if late and modified:
            reasons.append(f"last modified {day(modified)}, after the claim was submitted on {day(submitted[2])}"
                           if submitted else f"last modified {day(modified)}")
        page = next((p.id for p in ctx.pages if p.file_id == f.id), None)
        flags.append(Flag(
            f"File '{f.name}' shows signs of editing",
            f"The file was {' and '.join(reasons)}. Hospital documents are normally printed from the hospital "
            "system or scanned; ask for the originals.",
            [Evidence(f.name, page, None, f"Software: {software or '-'}; modified: {day(modified)}")],
            key=f"{f.name}|{software}|{modified}"))
    return flagged(*flags, detail=f"{len(ctx.files)} file(s) checked.")


@check("F-03")
def repeated_pages(ctx: ClaimContext) -> Result:
    hashed = [p for p in ctx.pages if p.image_hash]
    if len(hashed) < 2:
        return not_evaluable("Page images not available.") if not hashed else not_applicable("Single page.")
    limit = ctx.config_of("F-03").get("max_hash_distance", 6)
    doc_of = {pid: d for d in ctx.docs for pid in d.page_ids}
    flags = []
    for i, a in enumerate(hashed):
        for b in hashed[i + 1:]:
            if a.doc_type != b.doc_type or distance(a.image_hash or "", b.image_hash or "") > limit:
                continue
            da, db_ = doc_of.get(a.id), doc_of.get(b.id)
            if da is None or db_ is None or da is db_:
                continue
            differ = []
            slip = ctx.scanned(da) or ctx.scanned(db_)
            na, nb = da.fields.get("patient_name"), db_.fields.get("patient_name")
            if na and nb and ctx.usable(da, na) and ctx.usable(db_, nb) and not same_person(
                    na.text, nb.text, lenient=slip, word_similarity=0.6 if slip else 0.75):
                differ.append(("patient name", na, nb))
            ua, ub = da.fields.get("uhid"), db_.fields.get("uhid")
            if ua and ub and ctx.usable(da, ua) and ctx.usable(db_, ub) and not same_code(
                    ua.text, ub.text, lenient=slip):
                differ.append(("UHID", ua, ub))
            if not differ:
                continue
            what, va, vb = differ[0]
            flags.append(Flag(
                f"Same {label(a.doc_type)} page used twice with different {what}",
                f"Two {label(a.doc_type)} pages are near-identical images, but one shows {what} '{va.text}' and "
                f"the other '{vb.text}'. A page copied from another patient's file is a strong fraud signal.",
                [ctx.ev(da, va), ctx.ev(db_, vb)], key=f"{a.id}|{b.id}"))
    return flagged(*flags, detail=f"{len(hashed)} page images compared.")


# ---------------------------------------------------------------- F-02 / F-04 from the fonts in the PDF

AMOUNT = re.compile(r"^\(?(?:Rs\.?|₹)?\)?\d{1,3}(?:,\d{2,3})*(?:\.\d{2})$|^\d+\.\d{2}$")


@check("F-02")
def amount_font(ctx: ClaimContext) -> Result:
    bills = [d for d in ctx.docs if d.doc_type in ("final_bill", "pharmacy_bill")]
    if not bills:
        return not_applicable("No bill in the file.")
    min_amounts = ctx.config_of("F-02").get("min_amounts_on_page", 5)
    flags, read_pages, scanned = [], 0, 0
    for doc in bills:
        for pid in doc.page_ids:
            page = ctx.page(pid)
            words = ctx.pdf_words(page) if page else None
            if words is None:
                scanned += 1
                continue
            amounts = [w for w in words if AMOUNT.match(w.text)]
            if len(amounts) < min_amounts:
                continue
            read_pages += 1
            usual = Counter(w.family for w in amounts).most_common(1)[0][0]
            for w in amounts:
                if w.family == usual:
                    continue
                line = next((x for x in ctx.bill_lines if x.doc is doc and x.amount is not None
                             and f"{x.amount:,.2f}" == w.text.replace("Rs.", "").strip("()")), None)
                what = f"'{line.description}'" if line else "an amount"
                flags.append(Flag(
                    f"Amount in a different font on the {label(doc.doc_type)}: {w.text}",
                    f"On the {label(doc.doc_type)}, {what} shows {w.text} in {w.font}, while every other amount "
                    f"on the page is in {usual.title()}. A figure typed over a printed bill usually shows up "
                    "like this; compare it with the hospital's own copy.",
                    [Evidence(doc.key, pid, w.bbox, f"{w.text} ({w.font})")],
                    amount_at_stake=line.amount if line else None, key=f"{pid}|{w.text}|{w.font}"))
    if not read_pages:
        return not_evaluable("Bills are scans: fonts can only be compared in a digital PDF." if scanned
                             else "Too few amounts on the bill pages to compare.")
    return flagged(*flags, detail=f"Fonts of the amounts compared on {read_pages} bill page(s).")


HOSPITAL_DOCS = ("discharge_summary", "final_bill", "pharmacy_bill", "lab_report", "imaging_report", "ot_notes",
                 "nursing_chart")


@check("F-04")
def report_layout(ctx: ClaimContext) -> Result:
    from app.rules.pdf_fonts import header_family

    labs = ctx.docs_of("lab_report")
    if not labs:
        return not_applicable("No lab report in the file.")
    hospital = ctx.first("hospital_name")
    same_lab = [d for d in labs if "hospital_name" not in d.fields or hospital is None
                or similar(d.fields["hospital_name"].text, hospital[1].text) >= 0.8]
    fonts: dict[str, str] = {}
    for d in same_lab:
        first = ctx.page(d.page_ids[0]) if d.page_ids else None
        words = ctx.pdf_words(first) if first else None
        fam = header_family(words) if words else None
        if fam:
            fonts[d.key] = fam
    if fonts:
        counts = Counter(fonts.values())
        usual, top = counts.most_common(1)[0]
        if len(fonts) < 3 or top * 2 <= len(fonts):   # no clear majority among the lab reports: use the hospital's
            others = [header_family(ctx.pdf_words(p) or []) for p in ctx.pages
                      if p.doc_type in HOSPITAL_DOCS and p.doc_type != "lab_report"]
            hospital_counts = Counter(f for f in others if f)
            if not hospital_counts:
                return not_evaluable("No other hospital document to compare the lab reports with.")
            usual = hospital_counts.most_common(1)[0][0]
        flags = []
        for d in same_lab:
            fam = fonts.get(d.key)
            if fam is None or fam == usual:
                continue
            test = d.fields.get("test")
            flags.append(Flag(
                f"Lab report printed differently from the same lab's other reports: "
                f"{test.text if test else d.key}",
                f"This report's letterhead is printed in {fam.title()}; the lab's other reports and the hospital's "
                f"documents use {usual.title()}. A report from the same lab that looks different may have been "
                "made elsewhere. (If it comes from an outside lab, a different layout is normal.)",
                [ctx.ev(d, test) if test else doc_evidence(ctx, d, "Lab report")], key=f"{d.key}|{fam}"))
        return flagged(*flags, detail=f"Letterhead fonts of {len(fonts)} lab report(s) compared.")
    # scans: compare the column order of the results tables instead
    orders: dict[str, str] = {}
    for d in same_lab:
        rows = d.tables.get("results") or []
        boxes = next(((r["value"].bbox, r["reference"].bbox) for r in rows
                      if "value" in r and "reference" in r and r["value"].bbox and r["reference"].bbox), None)
        if boxes and boxes[0] and boxes[1]:
            orders[d.key] = "value-first" if boxes[0][0] < boxes[1][0] else "reference-first"
    if len(orders) >= 3:
        usual, top = Counter(orders.values()).most_common(1)[0]
        if top * 2 > len(orders):
            flags = [Flag("Lab report laid out differently from the same lab's other reports",
                          f"The results table puts the columns in a different order ({order}) from the lab's other "
                          f"reports ({usual}). It may not come from the same lab.",
                          [doc_evidence(ctx, d, "Results table")], key=f"{key}|{order}")
                     for key, order in orders.items() if order != usual
                     for d in [next(x for x in same_lab if x.key == key)]]
            return flagged(*flags, detail=f"Column order of {len(orders)} lab report(s) compared.")
    return not_evaluable("Lab reports are scans and there are too few to compare their layout.")
