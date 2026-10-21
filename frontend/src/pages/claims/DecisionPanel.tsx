import { useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";
import {
  RECOMMENDATION_LABELS,
  type Finding,
  type FindingsResponse,
  type QueryLetter,
  type Recommendation,
  type ReviewSummary,
} from "../../api/types";
import { money, when } from "./common";

interface Line {
  key: string;
  description: string;
  amount: string;
  finding_id: string | null;
}

const ORDER: Recommendation[] = ["approve", "approve_with_deductions", "query_hospital", "escalate", "refer_siu"];
const CLOSING: Recommendation[] = ["approve", "approve_with_deductions"];

/** Deduction lines the findings suggest: confirmed or open findings with a suggested deduction. */
function suggested(findings: Finding[]): Line[] {
  return findings
    .filter((f) => f.is_current && f.status !== "dismissed" && Number(f.suggested_deduction ?? 0) > 0)
    .map((f) => ({ key: f.id, description: `${f.rule_id}: ${f.title}`.slice(0, 300), amount: String(f.suggested_deduction), finding_id: f.id }));
}

export default function DecisionPanel({ review, onDecided, refreshKey }: {
  review: ReviewSummary;
  onDecided: (r: ReviewSummary) => void;
  refreshKey: string;
}) {
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [reason, setReason] = useState("");
  const [letter, setLetter] = useState<QueryLetter | null>(null);
  const [letterText, setLetterText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Pre-fill deduction lines from the findings when "approve with deductions" is picked.
  useEffect(() => {
    if (recommendation !== "approve_with_deductions") return;
    api
      .get<FindingsResponse>(`/claims/${review.claim_id}/findings`)
      .then((r) => setLines((current) => (current.length ? current : suggested(r.findings))))
      .catch(() => undefined);
  }, [recommendation, review.claim_id, refreshKey]);

  useEffect(() => {
    if (recommendation !== "query_hospital") return;
    api
      .get<QueryLetter>(`/claims/${review.claim_id}/query-letter`)
      .then((l) => {
        setLetter(l);
        setLetterText(l.body);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not draft the letter"));
  }, [recommendation, review.claim_id, refreshKey]);

  const total = useMemo(() => lines.reduce((sum, l) => sum + (Number(l.amount) || 0), 0), [lines]);
  const claimed = review.claimed_amount !== null ? Number(review.claimed_amount) : null;
  const approved = claimed !== null ? claimed - (recommendation === "approve_with_deductions" ? total : 0) : null;
  const blocked = recommendation !== null && CLOSING.includes(recommendation) && review.approve_blockers.length > 0;
  const linesOk = recommendation !== "approve_with_deductions" ||
    (lines.length > 0 && lines.every((l) => l.description.trim() && Number(l.amount) > 0) && (claimed === null || total <= claimed));

  const submit = async () => {
    if (!recommendation) return;
    setBusy(true);
    try {
      const body = {
        recommendation,
        reason: reason.trim(),
        deductions: recommendation === "approve_with_deductions"
          ? lines.map((l) => ({ description: l.description.trim(), amount: Number(l.amount).toFixed(2), finding_id: l.finding_id }))
          : [],
      };
      onDecided(await api.post<ReviewSummary>(`/claims/${review.claim_id}/decisions`, body));
      setRecommendation(null);
      setLines([]);
      setReason("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the recommendation");
    } finally {
      setBusy(false);
    }
  };

  const copyLetter = async () => {
    try {
      await navigator.clipboard.writeText(letterText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Could not copy: select the text and copy it yourself.");
    }
  };

  const history = review.decisions.length > 0 && (
    <ul className="decision-history">
      {[...review.decisions].reverse().map((d) => (
        <li key={d.id}>
          <strong>{RECOMMENDATION_LABELS[d.recommendation]}</strong>
          {d.approved_amount !== null && <> · payable {money(d.approved_amount, false)}</>} · {d.decided_by ?? "—"},{" "}
          {when(d.created_at)}
          <div className="muted">{d.reason}</div>
          {d.deductions.length > 0 && (
            <ul className="small">
              {d.deductions.map((x, i) => (
                <li key={i}>
                  {x.description}: {money(x.amount, false)}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );

  if (!review.can_decide) {
    return (
      <section className="card decision-panel">
        <h2 className="section-title">Decision</h2>
        {review.status === "decided" ? (
          <p className="notice-info">This claim is decided.</p>
        ) : review.escalated ? (
          <p className="muted">Escalated: waiting for a senior reviewer.</p>
        ) : (
          <p className="muted">Your role does not record recommendations on this claim.</p>
        )}
        {history}
      </section>
    );
  }

  return (
    <section className="card decision-panel">
      <h2 className="section-title">Decision</h2>
      {review.escalated && <p className="notice-warn">Escalated to you as senior reviewer.</p>}
      <p className="muted small">
        ClaimGuard only recommends. Your recommendation, with its reason, is what goes on the claim file.
      </p>
      <fieldset className="recommendations">
        <legend className="sr-only">Recommendation</legend>
        {ORDER.map((r) => (
          <label key={r} className={`radio-card ${recommendation === r ? "active" : ""}`}>
            <input type="radio" name="recommendation" checked={recommendation === r} onChange={() => setRecommendation(r)} />
            {RECOMMENDATION_LABELS[r]}
          </label>
        ))}
      </fieldset>

      {blocked && (
        <ul className="notice-warn blockers">
          {review.approve_blockers.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      )}

      {recommendation === "approve_with_deductions" && (
        <div className="deductions">
          <table className="table compact-table">
            <thead>
              <tr>
                <th>Deduction</th>
                <th className="num">Amount (₹)</th>
                <th aria-label="Remove" />
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={l.key}>
                  <td>
                    <input
                      value={l.description}
                      onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))}
                      aria-label="Deduction description"
                    />
                  </td>
                  <td className="num">
                    <input
                      className="amount-input"
                      inputMode="decimal"
                      value={l.amount}
                      onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))}
                      aria-label="Deduction amount"
                    />
                  </td>
                  <td>
                    <button className="link-button" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
              <tr>
                <td>
                  <button
                    className="link-button"
                    onClick={() => setLines([...lines, { key: `new-${Date.now()}-${lines.length}`, description: "", amount: "", finding_id: null }])}
                  >
                    + Add a line
                  </button>
                </td>
                <td className="num">
                  <strong>{money(total, false)}</strong>
                </td>
                <td />
              </tr>
            </tbody>
          </table>
          {claimed !== null && total > claimed && <p className="error-text small">Deductions are more than the claimed amount.</p>}
        </div>
      )}

      {recommendation && CLOSING.includes(recommendation) && approved !== null && (
        <p>
          Payable: <strong>{money(approved, false)}</strong> <span className="muted">of {money(claimed, false)} claimed</span>
        </p>
      )}

      {recommendation === "query_hospital" && letter && (
        <div className="letter">
          <div className="letter-head">
            <strong>Query letter draft</strong>
            <span className="muted small">
              {letter.documents.length} document(s), {letter.clarifications.length} clarification(s) from the findings. Edit, then copy
              it into your email or letter.
            </span>
            <button className="button button-quiet" onClick={copyLetter}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <textarea value={letterText} onChange={(e) => setLetterText(e.target.value)} rows={14} aria-label="Query letter" />
        </div>
      )}

      {recommendation && (
        <label className="block">
          Reason (goes on the claim file)
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
        </label>
      )}
      {error && <p className="error-text" role="alert">{error}</p>}
      {recommendation && (
        <div className="finding-actions">
          <button className="button" onClick={submit} disabled={busy || blocked || !linesOk || reason.trim().length < 5}>
            {busy ? "Saving…" : `Record: ${RECOMMENDATION_LABELS[recommendation]}`}
          </button>
          <button className="link-button" onClick={() => setRecommendation(null)}>
            Cancel
          </button>
        </div>
      )}
      {history}
    </section>
  );
}
