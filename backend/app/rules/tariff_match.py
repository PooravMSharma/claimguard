"""Find the benchmark item (CGHS, PM-JAY or a client tariff) that a hospital bill line is for.

Hospital bills and rate lists name the same thing differently: "CBC" / "Complete Haemogram/CBC, Hb, RBC count
and indices, TLC, DLC ...", "USG Abdomen" / "USG Whole Abdomen Including Pelvis ...". Exact wording almost never
matches, so a bill line is compared with a tariff item in four steps:

1. A mapping someone confirmed ("Hb%" -> LB007) always wins (tariff_mappings, loaded by an admin).
2. Both texts are reduced to their core: the part before the first comma, without brackets, with common
   abbreviations and spellings mapped to one word each (CBC = complete blood count = haemogram -> "cbc").
3. Each core becomes a set of words. Words that appear in few tariff items (e.g. "cholecystectomy") count more
   than common ones ("scan"). The score is high when the bill's words are all in the item and the item adds
   little the bill does not say.
4. A line and an item must be the same kind of charge: a test matches a test, a consultation a consultation,
   a package a package. A surgeon's fee never matches a CGHS procedure rate, which covers the whole package.

The best item is used only when its score is at least `min_match_score` (B-03 setting) and clearly better than
a different-priced runner-up. Otherwise the line is not compared: a wrong benchmark would be worse than none.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

# phrase -> one canonical word. Longest phrases first (applied in this order).
CANONICAL: list[tuple[str, str]] = [
    (r"complete blood (count|picture)|complete ha?emogram|ha?emogram|\bcbc\b", "cbc"),
    (r"liver function tests?|\blft\b", "lft"),
    (r"(renal|kidney) function tests?|\b(rft|kft)\b", "rft"),
    (r"thyroid (function tests?|profile)|\btft\b", "thyroidprofile"),
    (r"lipid profile", "lipidprofile"),
    (r"\brbs\b", "glucose random"),
    (r"\bfbs\b", "glucose fasting"),
    (r"\bpp ?bs\b", "glucose pp"),
    (r"blood sugar|blood glucose|plasma glucose", "glucose"),
    (r"ha?emoglobin\b|\bhb\b", "hb"),
    (r"electrocardiogra(m|phy)|\b(ecg|ekg)\b", "ecg"),
    (r"2 ?d ?echo(cardiography|cardiogram)?|echocardiogra(phy|m)|\becho\b", "echo"),
    (r"ultrasonography|ultrasound|sonography|\busg\b", "usg"),
    (r"x[ -]?ray|radiograph|\bcxr\b", "xray"),
    (r"magnetic resonance imaging|\bmri\b", "mri"),
    (r"computed tomography|\bct\b(?! ?scan)|\bct ?scan\b|\bncct\b|\bcect\b", "ct"),
    (r"without contrast|non[- ]?contrast|\bplain\b", "plain"),
    (r"with contrast|contrast enhanced", "contrast"),
    (r"laparoscopic|\blap\.?\b", "lap"),
    (r"appendi?c?ectomy|appendectomy", "appendicectomy"),
    (r"\bchole\b|cholecystectomy", "cholecystectomy"),
    (r"consultation|consultant visit|doctor'?s? visit|visit charges|\bvisit\b", "consultation"),
    (r"blood culture|culture (and|&) sensitivity|\bc ?& ?s\b", "culture"),
    (r"urine (routine|r ?/ ?m|re)\b|urine examination|\burine r\b", "urineroutine"),
    (r"serum electrolytes|\belectrolytes\b", "electrolytes"),
    (r"c[- ]?reactive protein|\bcrp\b", "crp"),
    (r"neonatal (icu|intensive care)|\bnicu\b", "nicu"),
    (r"\bpicu\b", "picu"),
    (r"intensive care unit|\b(icu|iccu|micu|sicu|ccu|hdu)\b", "icu"),
    (r"\bhead\b", "brain"),
    (r"\bantigen\b", "ag"),
    (r"\bantibod(y|ies)\b", "ab"),
    (r"\b(in[- ]?patients?|ipd)\b", "inpatient"),
    (r"\bwhole abdomen\b", "abdomen"),
]
STOP = {
    "the", "of", "for", "and", "or", "with", "in", "a", "an", "to", "per", "each", "by", "on", "at",
    "charge", "charges", "fee", "fees", "test", "tests", "investigation", "investigations", "examination",
    "procedure", "including", "inclusive", "only", "single", "one", "film", "view", "views", "study",
    "level", "estimation", "total", "serum", "dr", "mr", "mrs", "ms", "no", "nos", "type", "any", "other",
    "package", "pkg", "sr",
}
KIND_BY_PREFIX = {"LB": "test", "RI": "test", "NM": "test", "CI": "test", "EI": "test", "OI": "test", "NI": "test",
                  "GI": "test", "CN": "consultation", "CC": "critical_care", "WC": "room"}
KIND_BY_CATEGORY = {"investigation": "test", "consultation": "consultation", "package": "procedure",
                    "procedure": "procedure", "icu": "critical_care", "room": "room"}
# which item kinds a line kind may be compared with (a bedside procedure such as nebulisation is a CC item)
COMPATIBLE = {"procedure": {"procedure", "critical_care"}}


def core(text: str) -> str:
    """The naming part of an item: lower case, no brackets, the part before the first comma, words canonical."""
    t = text.lower()
    # brackets usually list contents ("(Hb, TLC, DLC...)"), but "(plain)" or "(contrast)" says which item it is
    kept = " ".join(m for b in re.findall(r"\((.*?)\)", t) for m in re.findall(r"plain|without contrast|contrast", b))
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t) + " " + kept
    t = t.split(",")[0]
    t = re.sub(r"(?i)^\s*(surgeon'?s?|anaesthetist'?s?|assistant surgeon'?s?) fees?\s*[-–:]\s*", "", t)
    for pattern, word in CANONICAL:
        t = re.sub(pattern, f" {word} ", t)
    # details after "including / with / excluding" describe what the item covers, not what it is
    t = re.split(r"\b(?:including|inclusive|with|excluding|except)\b", t)[0]
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def words(text: str) -> frozenset[str]:
    return frozenset(w for w in core(text).split() if w not in STOP)


def tariff_kind(code: str, specialty: str | None, category: str | None) -> str:
    """test / consultation / critical_care / room / procedure, from the CGHS code prefix or the item's group."""
    prefix = re.sub(r"[^A-Z]", "", code.upper())[:2]
    text = f"{specialty or ''} {category or ''}".lower()
    if prefix == "TM" or "cancer surgery" in text:
        return "cancer_procedure"       # CGHS: cancer surgeries at the Tata Memorial (TMH) rates
    if prefix in KIND_BY_PREFIX:
        return KIND_BY_PREFIX[prefix]
    # a client's own rate card: fee, theatre and nursing rows have no per-item counterpart on a bill line kind
    if re.search(r"professional|\bfees?\b", text):
        return "professional_fee"
    if re.search(r"theatre|\bot\b", text):
        return "ot"
    if "nursing" in text:
        return "nursing"
    if re.search(r"\broom\b|\bward\b|\bbed\b", text):
        return "room"
    if "investigation" in text or "laborator" in text or "radiolog" in text:
        return "test"
    if "consult" in text:
        return "consultation"
    return "procedure"


def line_kinds(category: str, cancer: bool = False) -> set[str] | None:
    """Item kinds a bill line may be compared with. None: no per-item benchmark (surgeon fee, OT, drugs...).
    A procedure on a cancer claim may also match the TMH cancer-surgery rates; on other claims it may not."""
    kind = KIND_BY_CATEGORY.get(category)
    if kind is None:
        return None
    kinds = set(COMPATIBLE.get(kind, {kind}))
    if kind == "procedure" and cancer:
        kinds.add("cancer_procedure")
    return kinds


@dataclass(frozen=True)
class Candidate:
    key: int                       # index into the caller's tariff list
    words: frozenset[str]
    kind: str | None               # None: any kind (client / synthetic tariffs without a kind)
    rate: float
    group: str = ""                # the list it comes from (cghs / client / pmjay)


@dataclass
class Match:
    key: int
    score: float
    how: str                       # "confirmed mapping" | "same wording" | "close wording (0.82)"


class Matcher:
    def __init__(self, candidates: list[Candidate]):
        self.candidates = candidates
        df: Counter = Counter()
        for c in candidates:
            df.update(c.words)
        n = max(1, len(candidates))
        self.idf = {w: math.log(1 + n / k) for w, k in df.items()}
        self.by_word: dict[str, list[int]] = {}
        for i, c in enumerate(candidates):
            for w in c.words:
                self.by_word.setdefault(w, []).append(i)

    def _weight(self, ws: frozenset[str]) -> float:
        return sum(self.idf.get(w, math.log(1 + len(self.candidates))) for w in ws)

    def score(self, bill: frozenset[str], item: frozenset[str]) -> float:
        if not bill or not item:
            return 0.0
        common = bill & item
        if not common:
            return 0.0
        covered = self._weight(common) / self._weight(bill)       # how much of the bill line the item explains
        focused = self._weight(common) / self._weight(item)       # how little the item adds
        return round(covered * (0.4 + 0.6 * focused), 3)

    def best(self, text: str, kinds: set[str] | None, min_score: float, margin: float = 0.08,
             groups: set[str] | None = None) -> Match | None:
        """kinds: the item kinds allowed (from line_kinds); None allows any (tariffs without kinds).
        groups: only items from these lists (None: any)."""
        bill = words(text)
        if not bill:
            return None
        pool = {i for w in bill for i in self.by_word.get(w, [])}
        scored = []
        for i in pool:
            c = self.candidates[i]
            if kinds is not None and c.kind and c.kind not in kinds:
                continue
            if groups is not None and c.group not in groups:
                continue
            s = 1.0 if c.words == bill else self.score(bill, c.words)
            if s >= min_score:
                scored.append((s, i))
        if not scored:
            return None
        scored.sort(reverse=True)
        top_score, top = scored[0]
        exact_rivals = [i for s, i in scored[1:] if s >= 1.0]
        if top_score >= 1.0 and not exact_rivals:
            return Match(self.candidates[top].key, top_score, "same wording")
        for s, i in scored[1:]:
            if top_score - s >= margin:
                break
            other = self.candidates[i].rate
            mine = self.candidates[top].rate
            if mine and abs(other - mine) / mine > 0.10:      # a close rival with a different price: unsure
                return None
        how = "same wording" if top_score >= 1.0 else f"close wording ({top_score:.2f})"
        return Match(self.candidates[top].key, top_score, how)


def build(items: list[tuple]) -> Matcher:
    """items: (name, code, kind, rate[, group]) in the caller's order; Match.key is the index into that list."""
    return Matcher([Candidate(i, words(it[0]), it[2], it[3], it[4] if len(it) > 4 else "")
                    for i, it in enumerate(items)])
