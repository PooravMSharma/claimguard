"""Match hospital bill lines against the IRDAI non-payable lists.

Each list item is turned into search terms (its name, the parts of its name and
its synonyms). A bill line matches a term when the term appears in it as whole
words. The longest matching term wins, so "oxygen mask" (List I) beats "mask".

Single short words such as "mask", "spacer" or "cotton" are easy to hit by
accident ("laryngeal mask airway", "interbody spacer", "Caps. Omez 20mg"), so a
one-word term only matches a short line that is not a medicine or a device.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reference import NonPayableItem
from app.services.reference.text import normalise

# Words that carry no meaning about what the item is.
GENERIC_WORDS = {
    "charge", "charges", "chg", "chgs", "cost", "per", "day", "days", "unit", "units", "nos", "no", "pcs", "pc",
    "piece", "pieces", "qty", "set", "pack", "disposable", "disp", "item", "items", "and", "of", "for", "the",
    "x", "with", "sterile", "size", "small", "medium", "large", "adult", "paediatric", "pediatric",
}
# A one-word term never matches a line that mentions one of these: it is a device or implant.
DEVICE_WORDS = {
    "implant", "cage", "stent", "airway", "prosthesis", "catheter", "cannula", "graft", "valve", "lens",
    "screw", "plate", "rod", "nail", "interbody", "cement", "laryngeal", "lma", "mesh", "balloon", "wire",
}
DOSAGE = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|ml|gm|g|iu|units?)\b|\b(tab|tabs|cap|caps|inj|syp|syr)\b")
MEDICINE = re.compile(r"\b(tab|tabs|tablet|cap|caps|capsule|inj|injection|syp|syr|syrup)\b")
MEASURE = re.compile(r"\b\d[\w.]*\b")
TRAILING_NOISE = re.compile(r"\b(etc|any type|of any type)\b")
FUZZY_THRESHOLD = 0.9


@dataclass(frozen=True)
class Term:
    text: str
    words: int
    item_id: int
    list_number: int
    item_name: str
    notes: str | None


@dataclass(frozen=True)
class NonPayableMatch:
    item_id: int
    list_number: int
    item_name: str
    matched_term: str
    method: str  # "exact" | "fuzzy"
    score: float
    notes: str | None


def terms_for(item_name: str, synonyms: list[str]) -> set[str]:
    """Search terms for one list item."""
    without_brackets = re.sub(r"\(.*?\)", " ", item_name)
    terms = {normalise(without_brackets)}
    for piece in re.split(r"[/,]| - ", without_brackets):
        piece = normalise(TRAILING_NOISE.sub(" ", piece.lower()))
        if piece and set(piece.split()) - GENERIC_WORDS:
            terms.add(piece)
    terms.update(normalise(s) for s in synonyms)
    return {t for t in terms if t}


class NonPayableMatcher:
    def __init__(self, items: list[NonPayableItem]):
        terms: list[Term] = []
        for item in items:
            for text in terms_for(item.item_name, item.synonyms or []):
                terms.append(Term(text, len(text.split()), item.id, item.list_number, item.item_name, item.notes))
        # Longest terms first, so the most specific one wins.
        self.terms = sorted(terms, key=lambda t: (-len(t.text), t.list_number))
        self._patterns = {t.text: re.compile(rf"\b{re.escape(t.text)}\b") for t in self.terms}

    @classmethod
    def from_db(cls, db: Session) -> "NonPayableMatcher":
        return cls(list(db.scalars(select(NonPayableItem))))

    def _single_word_allowed(self, raw: str, line: str) -> bool:
        words = set(line.split())
        if words & DEVICE_WORDS or DOSAGE.search(raw.lower()):
            return False
        meaningful = [w for w in line.split() if w not in GENERIC_WORDS and not w.isdigit()]
        return len(meaningful) <= 3

    def match(self, description: str) -> NonPayableMatch | None:
        line = normalise(description)
        if not line:
            return None
        for term in self.terms:
            if self._patterns[term.text].search(line):
                if term.words == 1 and not self._single_word_allowed(description, line):
                    continue
                return self._result(term, "exact", 1.0)

        # Typos and run-together words ("glovs", "urinebag"): compare the meaningful part of the line.
        if MEDICINE.search(description.lower()):
            return None
        core = " ".join(w for w in MEASURE.sub(" ", line).split() if w not in GENERIC_WORDS)
        if len(core) < 5:
            return None
        best: tuple[float, Term] | None = None
        for term in self.terms:
            if len(term.text) < 5:
                continue
            score = max(SequenceMatcher(None, core, term.text).ratio(),
                        SequenceMatcher(None, core.replace(" ", ""), term.text.replace(" ", "")).ratio())
            if score >= FUZZY_THRESHOLD and (best is None or score > best[0]):
                best = (score, term)
        return self._result(best[1], "fuzzy", round(best[0], 3)) if best else None

    @staticmethod
    def _result(term: Term, method: str, score: float) -> NonPayableMatch:
        return NonPayableMatch(term.item_id, term.list_number, term.item_name, term.text, method, score, term.notes)
