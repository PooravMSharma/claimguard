# Step 11: risk score and review screen

## Risk score and band (`backend/app/review/scoring.py`)

| Band | Rule | Suggested action |
|---|---|---|
| Red | Any red finding, or score >= 70 | Senior review; investigation if forensic or identity findings |
| Amber | Any amber finding, or score 30–69 | Adjudicator reviews the listed findings |
| Green | Only info findings, score < 30 | Fast-track review |

- Score = sum of the weights of the rules that raised a finding (each rule once), capped at 100. Weights come
  from the rules table (defaults: red 30, amber 15, info 5).
- Medical-necessity findings alone cannot push a claim to red.
- Dismissed findings stop counting, so the band follows the review.
- Suggested payable = claimed amount minus the suggested deductions of findings that are not dismissed.
- The claim is re-scored after every checks run, review and decision. `python -m app.cli rescore` re-scores
  stored claims without running the checks.

## Deadlines (`backend/app/review/sla.py`)

IRDAI Master Circular on Health Insurance (2024): final cashless 3 hours, pre-authorisation 1 hour,
reimbursement 15 days. The clock starts when the claim is uploaded. Synthetic test claims have no deadline.

## Queue

Default order: claims waiting for review first, then red, amber, green, then score, then deadline. Filters by
status, band and "assigned to me"; search covers claim no., policy no., patient and hospital. The time-left
column turns amber near the deadline and red when overdue. Claims kept open by a query, an escalation or an
SIU referral show that under their status.

## Review screen (claim page, "Review" tab)

- Header: band and score with "how the score adds up", patient, policy, hospital, diagnosis, stay, claimed vs
  suggested payable, deadline, assignee (team lead: any reviewer; reviewer: take it or hand it back).
- Left: findings ranked open first, then severity, then money at stake. Right: the page, with the evidence
  highlighted when a finding is selected.
- Keys: `j`/`k` next/previous, `c` confirm, `d` dismiss, `n` note, `e` show evidence, `←`/`→` pages,
  Ctrl+Enter save, Esc cancel.
- A note never settles a finding. Medical-necessity findings are confirmed or dismissed by a senior reviewer.

## Decision panel

Approve, approve with deductions (lines pre-filled from the findings' suggested deductions), query hospital
(letter draft from missing documents and confirmed findings), escalate to senior reviewer, refer to
investigation. A reason is required. Approval needs every red and amber finding reviewed. After an escalation,
only a senior reviewer decides. Approvals close the claim; the other recommendations keep it in review.
Nothing is rejected automatically.

## Measured

- 263 tests pass (16 new in `tests/test_review.py`).
- Offline on the 300 clean synthetic claims (rules only): the stored band matches the seeded band on 286/300.
  All 14 differences are claims whose only seeded issue is an AI check, which needs the model.
