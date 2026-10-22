# Step 12: rule library and reference data administration

## Rule library (`/admin/rules`, API `/api/rules`)

Everyone with `rules.view` (adjudicators, senior reviewers, investigators, team leads, admins) sees every check
with its status, highest severity, weight, thresholds, source and how reviewers judged its findings in the last
90 days. Admins (`rules.manage`) change it.

| Setting | Limits |
|---|---|
| Status | Non-medical rules: active / disabled. Medical-necessity rules: draft (amber, needs medical review) / clinician-reviewed / disabled. Clinician-reviewed needs the clinician's name. |
| Weight | 0–100 risk-score points. |
| Highest severity | Can be lowered, never raised above the severity the check was designed for. |
| Thresholds | Only the keys the check reads (`rules/defaults.py`), each keeping its type. Word lists one per line; per-item limits (e.g. consumables per day) can gain or lose items. |
| Source | Title and link of the guideline, circular or rate list. |

Every change needs a reason, bumps the rule version (findings keep the version that raised them), is stored in
`rule_changes` (migration 0009) and the audit log. Thresholds apply from the next checks run. Weight and status
changes re-score the claims still in review at once; a disabled rule's findings stop counting.

Dismissal tracking: a rule whose findings reviewers dismissed in 50% or more of at least 5 reviewed findings is
marked "retune". The rule page lists the latest dismissal reasons.

## Reference data (`/reference`)

- **Load a new version** (admins, `reference.manage`): PM-JAY packages, tariffs (CGHS, PM-JAY or the client's
  own; needs the date the rates take effect) and registries (`doctors.csv`, `hospitals.csv`, `watchlist.csv`,
  `policies.csv`). The same importers as the CLI run; a refused file changes nothing. Each import is recorded with
  its version, source and the file's SHA-256, and audit-logged.
- Several package lists can be loaded; for a code present in more than one, the most recently imported row is used.
- **Policy terms**: an admin edits a term's value (e.g. PED waiting months). The value keeps its shape; the old and
  new value and the reason go to the audit log.

## Measured

- 273 tests pass (10 new in `tests/test_rules_admin.py`).
- Offline check evaluation unchanged: clean 300 claims, recall 100%, precision 100%, no red on a genuine claim.
