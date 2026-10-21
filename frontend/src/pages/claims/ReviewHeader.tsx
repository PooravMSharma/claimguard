import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { STAGE_LABELS, type Person, type ReviewSummary } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { BandTag, TimeLeft, money, useNow, when } from "./common";

const date = (v: string | null) =>
  v ? new Date(v).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—";

/** Top of the review screen: risk band and why, the patient and stay, the money, deadline and assignee. */
export default function ReviewHeader({ review, onChanged }: { review: ReviewSummary; onChanged: (r: ReviewSummary) => void }) {
  const { user, can } = useAuth();
  const now = useNow();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lead = can("claims.assign");
  const decided = review.status === "decided";

  useEffect(() => {
    if (!lead) return;
    api.get<Person[]>("/review/assignees").then(setPeople).catch(() => setPeople([]));
  }, [lead]);

  const assign = async (userId: string | null) => {
    try {
      onChanged(await api.post<ReviewSummary>(`/claims/${review.claim_id}/assign`, { user_id: userId }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the assignment");
    }
  };

  const patient = [review.patient_name, review.age !== null ? `${review.age} y` : null, review.gender?.[0]?.toUpperCase()]
    .filter(Boolean)
    .join(", ");
  const mine = review.assignee?.id === user?.id;

  return (
    <div className={`card review-head band-edge-${review.band ?? "none"}`}>
      <div className="review-risk">
        <BandTag band={review.band} score={review.score} />
        <div>
          <strong>{review.band_reason}</strong>
          <div className="muted small">{review.band ? review.suggested_action : "Checks have not run yet."}</div>
        </div>
        {review.score_parts.length > 0 && (
          <details className="score-parts">
            <summary className="link-button">How the score adds up</summary>
            <table className="table compact-table">
              <tbody>
                {review.score_parts.map((p) => (
                  <tr key={p.rule_id}>
                    <td>
                      <code>{p.rule_id}</code>
                    </td>
                    <td>
                      {p.name}
                      {p.findings > 1 ? ` (${p.findings} findings)` : ""}
                      {p.medical && <span className="tag">medical</span>}
                    </td>
                    <td>
                      <span className={`sev sev-${p.severity}`}>{p.severity}</span>
                    </td>
                    <td className="num">+{p.weight}</td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={3} className="muted">
                    Score (sum of rule weights, max 100)
                    {review.dismissed_findings > 0 && `; ${review.dismissed_findings} dismissed finding(s) not counted`}
                  </td>
                  <td className="num">
                    <strong>{review.score}</strong>
                  </td>
                </tr>
              </tbody>
            </table>
          </details>
        )}
      </div>

      <dl className="facts review-facts">
        <div>
          <dt>Patient</dt>
          <dd>{patient || "—"}</dd>
        </div>
        <div>
          <dt>Policy</dt>
          <dd>
            {review.policy_number ?? "—"}
            {money(review.sum_insured) && <div className="muted small">Sum insured {money(review.sum_insured)}</div>}
          </dd>
        </div>
        <div>
          <dt>Hospital</dt>
          <dd>
            {review.hospital_name ?? "—"}
            {review.rohini_id && <div className="muted small">Rohini {review.rohini_id}</div>}
          </dd>
        </div>
        <div>
          <dt>Diagnosis</dt>
          <dd>
            {review.diagnosis ?? "—"}
            {review.icd_code && <span className="muted"> ({review.icd_code})</span>}
            {review.procedures.length > 0 && <div className="muted small">{review.procedures.join("; ")}</div>}
          </dd>
        </div>
        <div>
          <dt>Stay</dt>
          <dd>
            {date(review.admission_at)} – {date(review.discharge_at)}
            {review.stay_nights !== null && <div className="muted small">{review.stay_nights} night(s) · {STAGE_LABELS[review.stage]}</div>}
          </dd>
        </div>
        <div className="money-facts">
          <dt>Claimed → suggested payable</dt>
          <dd>
            <strong>{money(review.claimed_amount, false) ?? "—"}</strong>
            {" → "}
            <strong>{money(review.suggested_payable, false) ?? "—"}</strong>
            <div className="muted small">
              {money(review.suggested_deduction) ? `Suggested deductions ${money(review.suggested_deduction)}` : "No suggested deductions"}
              {money(review.amount_at_stake) ? ` · at stake ${money(review.amount_at_stake)}` : ""}
            </div>
          </dd>
        </div>
        <div>
          <dt>Deadline</dt>
          <dd>
            <TimeLeft due={review.sla_due_at} done={decided} now={now} />
            {review.sla_due_at && <div className="muted small">{when(review.sla_due_at)}</div>}
          </dd>
        </div>
        <div>
          <dt>Assigned to</dt>
          <dd>
            {lead && people && !decided ? (
              <select value={review.assignee?.id ?? ""} onChange={(e) => assign(e.target.value || null)} aria-label="Assign to">
                <option value="">Nobody</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            ) : (
              review.assignee?.name ?? <span className="muted">Nobody</span>
            )}
            {!lead && can("claims.review") && !decided && (
              <div>
                {!review.assignee ? (
                  <button className="link-button" onClick={() => user && assign(user.id)}>
                    Assign to me
                  </button>
                ) : mine ? (
                  <button className="link-button" onClick={() => assign(null)}>
                    Hand back
                  </button>
                ) : null}
              </div>
            )}
          </dd>
        </div>
      </dl>
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}
