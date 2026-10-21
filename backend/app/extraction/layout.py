"""Read structure from positioned words: lines, labelled fields, section headings and tables.

Works the same on digital text and OCR words, and on any hospital's layout that
uses common labels ("Policy No.", "Admitted", "Bill No."...) and table headers.
"""

import re
from dataclasses import dataclass

from app.extraction.model import Value
from app.extraction.values import clean

NUMBER = re.compile(r"[-+]?[\d,]*\.?\d+%?")
GAP = 0.012           # horizontal gap (page fraction) that separates a label from the value before it
SEPARATORS = {"|", ":", "-", "–"}


@dataclass
class W:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float
    page_id: str
    page_no: int      # position of the page inside the document (0, 1, ...)
    line: int | None = None   # reading line from the text layer / OCR engine, when known

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def token(text: str) -> str:
    return re.sub(r"[^a-z0-9/&#%]", "", text.lower())


GLUED = re.compile(r"^(.*?\))(\d[\d,.]*)$")   # "(Rs.)85,100.00": label and value printed without a space


def words_of(page_id: str, page_no: int, raw: list | None) -> list[W]:
    out = []
    for r in raw or []:
        text = str(r[0]).strip()
        if not text:
            continue
        line = int(r[6]) if len(r) > 6 and r[6] is not None else None
        m = GLUED.match(text)
        if m:
            split = r[2] + (r[4] - r[2]) * len(m.group(1)) / len(text)
            out.append(W(m.group(1), float(r[1]), r[2], r[3], split, r[5], page_id, page_no, line))
            out.append(W(m.group(2), float(r[1]), split + 0.013, r[3], r[4], r[5], page_id, page_no, line))
        else:
            out.append(W(text, float(r[1]), r[2], r[3], r[4], r[5], page_id, page_no, line))
    return out


def group_lines(words: list[W]) -> list[list[W]]:
    """Visual lines, top to bottom, page by page.

    OCR words carry the engine's own line number, which stays right on slightly tilted scans; it is used
    for OCR pages. Text-layer pages are grouped by position (their line numbers follow the PDF's drawing
    order, which can differ from what you see).
    """
    out: list[list[W]] = []
    pages = sorted({w.page_no for w in words})
    for page in pages:
        ws = [w for w in words if w.page_no == page]
        ocr = ws and all(w.line is not None for w in ws) and any(w.conf < 1.0 for w in ws)
        if ocr:
            by_line: dict[int, list[W]] = {}
            for w in ws:
                by_line.setdefault(w.line, []).append(w)   # type: ignore[arg-type]
            lines = sorted(by_line.values(), key=lambda ln: sum(w.cy for w in ln) / len(ln))
            out += [sorted(ln, key=lambda w: w.x0) for ln in lines]
            continue
        lines: list[list[W]] = []
        for w in sorted(ws, key=lambda w: (w.cy, w.x0)):
            if lines:
                cur = lines[-1]
                top = min(x.y0 for x in cur)
                bottom = max(x.y1 for x in cur)
                height = max(bottom - top, 0.004)
                if top - 0.25 * height <= w.cy <= bottom + 0.25 * height:
                    cur.append(w)
                    continue
            lines.append([w])
        out += [sorted(ln, key=lambda w: w.x0) for ln in lines]
    return out


def line_text(line: list[W]) -> str:
    return " ".join(w.text for w in line)


def make_value(words: list[W], normalized=None, text: str | None = None) -> Value | None:
    while words and words[0].text in SEPARATORS:
        words = words[1:]
    while words and words[-1].text in SEPARATORS:
        words = words[:-1]
    if not words:
        return None
    raw = clean(text if text is not None else " ".join(w.text for w in words))
    if not raw:
        return None
    page_id = words[0].page_id
    same = [w for w in words if w.page_id == page_id]
    bbox = [min(w.x0 for w in same), min(w.y0 for w in same), max(w.x1 for w in same), max(w.y1 for w in same)]
    conf = min(w.conf for w in words)
    return Value(raw, normalized, page_id, [round(b, 4) for b in bbox], conf)


# ---------------------------------------------------------------- labelled fields


@dataclass
class Label:
    tokens: list[str]
    field: str


def compile_labels(labels: dict[str, str]) -> list[Label]:
    out = [Label([t for t in (token(x) for x in phrase.split()) if t], name) for phrase, name in labels.items()]
    return sorted(out, key=lambda lab: -len(lab.tokens))


def _match_at(line: list[W], i: int, labels: list[Label]) -> tuple[Label, int] | None:
    if i > 0:
        prev = line[i - 1]
        if prev.text not in SEPARATORS and line[i].x0 - prev.x1 < GAP:
            return None
    for lab in labels:
        j, k = i, 0
        while j < len(line) and k < len(lab.tokens):
            t = token(line[j].text)
            if not t:          # punctuation-only word inside a label, e.g. "(" or ":"
                j += 1
                continue
            if t != lab.tokens[k]:
                break
            j, k = j + 1, k + 1
        if k == len(lab.tokens):
            return lab, j
    return None


def find_labels(line: list[W], labels: list[Label]) -> list[tuple[int, int, Label]]:
    """[(start, end, label)] for each label on the line (end = index after the label)."""
    found, i = [], 0
    while i < len(line):
        hit = _match_at(line, i, labels)
        if hit:
            found.append((i, hit[1], hit[0]))
            i = hit[1]
        else:
            i += 1
    return found


def labelled_fields(lines: list[list[W]], labels: dict[str, str],
                    multiline: set[str] | frozenset[str] = frozenset(),
                    valid: dict[str, re.Pattern] | None = None) -> dict[str, Value]:
    """Value after each label, up to the next label on the line. Fields in `multiline` take following lines
    that sit under the value (same column) and carry no label of their own."""
    compiled = compile_labels(labels)
    found_per_line = [find_labels(line, compiled) for line in lines]
    out: dict[str, Value] = {}
    for li, (line, found) in enumerate(zip(lines, found_per_line)):
        for n, (start, end, lab) in enumerate(found):
            if lab.field in out:   # first valid occurrence wins
                continue
            stop = found[n + 1][0] if n + 1 < len(found) else len(line)
            words = line[end:stop]
            if not words:
                continue
            if lab.field in multiline:
                left = words[0].x0 - 0.01
                column_left = line[start].x0 - 0.02          # where this field's label starts
                right = line[stop].x0 if stop < len(line) else 1.0
                bottom = max(w.y1 for w in words)
                for nxt, nfound in zip(lines[li + 1: li + 3], found_per_line[li + 1: li + 3]):
                    span = [w for w in nxt if left <= w.x0 < right]
                    # a label here means a new field starts - unless nothing follows it in this column
                    # (a wrapped value ending in a label word, e.g. "Sanjeevani Superspeciality / Hospital")
                    label_in_span = any(column_left <= nxt[s].x0 < right
                                        and any(w.x0 < right for w in nxt[e:]) for s, e, _ in nfound)
                    if not span or label_in_span or nxt[0].page_no != line[0].page_no \
                            or min(w.y0 for w in span) - bottom > 0.02:
                        break
                    words = words + span
                    bottom = max(w.y1 for w in span)
            value = make_value(words)
            pattern = (valid or {}).get(lab.field)
            if value and (pattern is None or pattern.search(value.text)):
                out[lab.field] = value
    return out


# ---------------------------------------------------------------- section headings (discharge summary)


def sections(lines: list[list[W]], headings: dict[str, str], stop: tuple[str, ...] = ("____", "(signature"),
             ) -> dict[str, Value]:
    """Text under each heading line, until the next heading."""
    head = {re.sub(r"[^a-z]", "", h.lower()): name for h, name in headings.items()}
    out: dict[str, list[W]] = {}
    current: str | None = None
    for line in lines:
        key = re.sub(r"[^a-z]", "", line_text(line).lower())
        if key in head:
            current = head[key]
            out.setdefault(current, [])
            continue
        if any(line_text(line).lower().startswith(s) for s in stop):
            current = None
            continue
        if current:
            out[current].extend(line)
    values = {}
    for name, words in out.items():
        v = make_value(words)
        if v:
            v.normalized = [line_text(ln) for ln in group_lines(words)]   # the section's lines, for lists
            values[name] = v
    return values


# ---------------------------------------------------------------- tables


@dataclass
class Table:
    columns: dict[str, list[str]]     # column name -> header phrases that may name it
    anchor: str                       # column that starts a row
    row_end: str                      # once a row has this, the next anchor starts a new row
    stop: tuple[str, ...] = ()        # a line starting with one of these ends the table


def _find_header(lines: list[list[W]], table: Table, start: int = 0):
    for li in range(start, len(lines)):
        line = lines[li]
        toks = [token(w.text) for w in line]
        centers: dict[str, tuple[float, float]] = {}   # column -> header (x0, x1)
        for col, phrases in table.columns.items():
            for phrase in phrases:
                want = [t for t in (token(x) for x in phrase.split()) if t]
                for i in range(len(toks) - len(want) + 1):
                    if toks[i:i + len(want)] == want and not any(
                            x0 <= line[i].x0 <= x1 for x0, x1 in centers.values()):
                        centers[col] = (line[i].x0, line[i + len(want) - 1].x1)
                        break
                if col in centers:
                    break
        if len(centers) >= max(3, int(0.6 * len(table.columns))) and table.anchor in centers:
            return li, centers
    return None


def parse_table(lines: list[list[W]], table: Table) -> list[dict[str, Value]]:
    rows: list[dict[str, list[W]]] = []
    li = 0
    stop_tokens = tuple(re.sub(r"[^a-z]", "", s) for s in table.stop) + ("signature",)
    while True:
        header = _find_header(lines, table, li)
        if header is None:
            break
        li, centers = header
        order = sorted(centers.items(), key=lambda kv: kv[1][0])
        bounds = []
        for k, (col, (x0, _)) in enumerate(order):
            left = x0 - 0.012 if k else 0.0
            right = order[k + 1][1][0] - 0.012 if k + 1 < len(order) else 1.0
            bounds.append((col, left, right))
        page = lines[li][0].page_no
        last_y = lines[li][0].y1
        current: dict[str, list[W]] | None = None
        li += 1
        while li < len(lines):
            line = lines[li]
            text = re.sub(r"[^a-z]", "", line_text(line).lower())
            if line[0].page_no != page or text.startswith(stop_tokens) or "____" in line_text(line) \
                    or line[0].y0 - last_y > 0.06:
                break
            cells: dict[str, list[W]] = {}
            for w in line:
                # numbers are usually right-aligned: place them by their right edge; text by its left edge
                x = w.x1 - 0.002 if NUMBER.fullmatch(w.text) else w.x0 + 0.002
                for col, left, right in bounds:
                    if left <= x < right:
                        cells.setdefault(col, []).append(w)
                        break
            # a new row needs the anchor column plus another column (a lone anchor word is wrapped text)
            new_row = table.anchor in cells and len(cells) > 1 and (current is None or table.row_end in current)
            if new_row or current is None:
                current = {}
                rows.append(current)
            for col, ws in cells.items():
                current.setdefault(col, []).extend(ws)
            last_y = max(w.y1 for w in line)
            li += 1
    out = []
    for row in rows:
        values = {col: v for col, ws in row.items() if (v := make_value(ws))}
        if table.row_end in values or table.anchor in values:
            out.append(values)
    return out