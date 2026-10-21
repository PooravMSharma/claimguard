"""F-layer (rule part): file metadata and pages used twice. Visual tampering checks (F-02, F-04) are AI."""

import io
import re
from datetime import datetime, timedelta

from app.rules.checks.common import day, label, same_code, same_person
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
