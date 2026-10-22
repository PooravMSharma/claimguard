"""Which tariff item each bill wording was matched to, for an admin to confirm (CLI tariff-match-report).

The report is a CSV with one row per distinct bill wording. Its "Bill wording" and "Tariff code" columns are
exactly what the tariff_mappings import reads, so the workflow is: run the report, correct or delete rows (fill
in a code where nothing was matched), then import the file as "Bill wording links" on Reference data. Confirmed
links are used before any matching from then on.
"""

import csv
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.rules.checks.billing import benchmark, line_ok
from app.rules.context import ClaimContext
from app.rules.tariff_match import core, line_kinds

HEADER = ["Bill wording", "Tariff code", "Tariff item", "Matched by", "Benchmark (Rs)", "Typical billed (Rs)",
          "Claims", "Example claim", "Bill category", "Tariff source"]


@dataclass
class Row:
    text: str
    category: str
    code: str = ""
    name: str = ""
    how: str = "not matched"
    source: str = ""
    benchmark: Decimal | None = None
    billed: list[Decimal] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)


def collect(contexts: Iterable[ClaimContext]) -> list[Row]:
    rows: dict[str, Row] = {}
    for ctx in contexts:
        skip = set(ctx.config_of("B-03").get("skip_categories", []))
        for line in ctx.bill_lines:
            category = line.category.value
            if category in skip or line_kinds(category) is None or not line.description.strip():
                continue
            text = core(line.description)
            if not text:
                continue
            row = rows.get(text)
            if row is None:
                row = rows[text] = Row(text=text, category=category)
                bench = benchmark(ctx, line)
                if bench is not None:
                    row.code, row.name, row.how = bench.ref.code, bench.ref.name, bench.how
                    row.source, row.benchmark = bench.ref.source, bench.rate
            if line.rate is not None and line_ok(ctx, line):
                row.billed.append(line.rate)
            if ctx.facts.claim_number not in row.claims:
                row.claims.append(ctx.facts.claim_number)
    # most frequent first: confirming those removes the most guessing
    return sorted(rows.values(), key=lambda r: (-len(r.claims), r.text))


def write_csv(rows: list[Row], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(HEADER)
        for r in rows:
            billed = sorted(r.billed)
            typical = billed[len(billed) // 2] if billed else None
            out.writerow([r.text, r.code, r.name[:200], r.how, r.benchmark or "", typical or "", len(r.claims),
                          r.claims[0] if r.claims else "", r.category, r.source])
