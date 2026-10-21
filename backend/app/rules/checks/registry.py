"""R-layer: the treating doctor and the hospital against the registers and the insurer's watchlist."""

from app.extraction.values import split_doctor
from app.rules.checks.common import code, label, lenient, rohini, similar
from app.rules.context import ClaimContext, Evidence
from app.rules.engine import Flag, Result, check, flagged, not_evaluable, passed


def _close_key(value: str, keys, *, allow_slip: bool) -> str | None:
    """The registry key for a printed ID, allowing one misread character when it came from a scan."""
    key = code(value)
    if key in keys:
        return key
    if allow_slip:
        for k in keys:
            if len(k) == len(key) and sum(1 for a, b in zip(k, key) if a != b) <= 1:
                return k
    return None


def _strip_title(name: str) -> str:
    return name.replace("Dr.", "").replace("Dr ", "").strip()


@check("R-01")
def doctor_registered(ctx: ClaimContext) -> Result:
    if ctx.ref.doctors is None:
        return not_evaluable("Doctor registry not loaded.")
    hit = ctx.first("doctor_reg_no")
    if hit is None:
        return not_evaluable("Doctor's registration number not read (see C-02).")
    doc, value = hit
    if not ctx.usable(doc, value):
        return not_evaluable("Registration number unclear on the scan.")
    registry = {code(k): v for k, v in ctx.ref.doctors.items()}
    key = _close_key(value.text, registry, allow_slip=lenient(ctx, doc))
    if key is None:
        return flagged(Flag(
            "Treating doctor's registration not found",
            f"Registration number {value.text} on the {label(doc.doc_type)} is not in the medical register. Verify "
            "with the state medical council before relying on this doctor's certification.",
            [ctx.ev(doc, value)], key=f"missing|{code(value.text)}"))
    printed = ctx.first("doctor_name")
    threshold = ctx.config_of("R-01").get("name_similarity", 0.8)
    if printed and ctx.usable(*printed) and similar(
            _strip_title(split_doctor(printed[1].text)[0] or printed[1].text),
            _strip_title(registry[key])) < threshold:
        return flagged(Flag(
            "Registration number belongs to another doctor",
            f"Registration {value.text} is registered to {registry[key]}, but the {label(printed[0].doc_type)} "
            f"names {printed[1].text}.", [ctx.ev(doc, value), ctx.ev(*printed)],
            key=f"name|{key}|{printed[1].text}"))
    return passed(f"{value.text} registered to {registry[key]}.")


@check("R-02")
def hospital_registered(ctx: ClaimContext) -> Result:
    if ctx.ref.hospitals is None:
        return not_evaluable("Hospital registry not loaded.")
    hit = ctx.first("rohini_id")
    if hit is None:
        return not_evaluable("Rohini ID not read.")
    doc, value = hit
    printed = rohini(value)
    if printed is None or not ctx.usable(doc, value):
        return not_evaluable("Rohini ID unclear.")
    key = _close_key(printed, ctx.ref.hospitals, allow_slip=lenient(ctx, doc))
    if key is None:
        return flagged(Flag(
            "Hospital not found in the registry",
            f"Rohini ID {value.text} on the {label(doc.doc_type)} is not in the hospital registry (HFR / Rohini). "
            "Verify the hospital before paying.", [ctx.ev(doc, value)], key=f"missing|{printed}"))
    registered = ctx.ref.hospitals[key]
    name = ctx.first("hospital_name")
    threshold = ctx.config_of("R-02").get("name_similarity", 0.75)
    if name and ctx.usable(*name) and similar(name[1].text, registered.name) < threshold:
        return flagged(Flag(
            "Rohini ID registered to a different hospital",
            f"Rohini ID {value.text} is registered to {registered.name}, {registered.city or ''}; the documents name "
            f"{name[1].text}.", [ctx.ev(doc, value), ctx.ev(*name)], key=f"name|{key}|{name[1].text}"))
    return passed(f"Registered: {registered.name}, {registered.city or ''}.")


@check("R-03")
def hospital_watchlist(ctx: ClaimContext) -> Result:
    if ctx.ref.watchlist is None:
        return not_evaluable("Watchlist not loaded.")
    hit = ctx.first("rohini_id")
    if hit is None:
        return not_evaluable("Rohini ID not read.")
    doc, value = hit
    printed = rohini(value)
    if printed is None:
        return not_evaluable("Rohini ID unclear.")
    key = _close_key(printed, ctx.ref.watchlist, allow_slip=False)
    if key is None:
        return passed("Hospital is not on the watchlist.")
    return flagged(Flag(
        "Hospital is on the watchlist",
        f"Rohini ID {value.text} is on the insurer's watchlist: {ctx.ref.watchlist[key]}. Apply enhanced review.",
        [ctx.ev(doc, value), Evidence("insurer watchlist", None, None, ctx.ref.watchlist[key])], key=key))
