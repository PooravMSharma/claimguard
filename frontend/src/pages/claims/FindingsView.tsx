import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";
import {
  CHECK_STATUS_LABELS,
  LAYER_LABELS,
  type Bbox,
  type CheckRunRow,
  type ChecksResponse,
  type Finding,
  type FindingsResponse,
} from "../../api/types";

interface Props {
  claimId: string;
  canReview: boolean;
  onShow: (pageId: string, bbox: Bbox | null) => void;
  refreshKey: string;
}

const LAYERS = ["completeness", "forensics", "consistency", "medical", "billing", "policy", "registry"];
const SEVERITY_LABEL = { red: "Red", amber: "Amber", info: "Info" } as const;

const money = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === "" || Number(v) === 0
    ? null
    : `₹${Number(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;

const when = (v: string) => new Date(v).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

function ReviewBox({ finding, onDone }: { finding: Finding; onDone: (f: Finding) => void }) {
  const [mode, setMode] = useState<"confirmed" | "dismissed" | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    if (!mode) return;
    setBusy(true);
    try {
      const updated = await api.post<Finding>(`/findings/${finding.id}/review`, { action: mode, reason: reason || null });
      onDone(updated);
      setMode(null);
      setReason("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the review");
    } finally {
      setBusy(false);
    }
  };

  if (!mode) {
    return (
      <div className="finding-actions">
        <button className="button button-quiet" onClick={() => setMode("confirmed")}>
          Confirm
        </button>
        <button className="button button-quiet" onClick={() => setMode("dismissed")}>
          Dismiss
        </button>
      </div>
    );
  }
  return (
    <div className="review-box">
      <label>
        {mode === "dismissed" ? "Why is this not a problem? (required)" : "Note (optional)"}
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} autoFocus />
      </label>
      {error && <p className="error-text">{error}</p>}
      <div className="finding-actions">
        <button className="button" onClick={send} disabled={busy || (mode === "dismissed" && !reason.trim())}>
          {busy ? "Saving…" : mode === "dismissed" ? "Dismiss finding" : "Confirm finding"}
        </button>
        <button className="link-button" onClick={() => setMode(null)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function FindingCard({ f, canReview, onShow, onReviewed }: {
  f: Finding;
  canReview: boolean;
  onShow: Props["onShow"];
  onReviewed: (f: Finding) => void;
}) {
  const atStake = money(f.amount_at_stake);
  const deduction = money(f.suggested_deduction);
  return (
    <article className={`finding finding-${f.severity} ${f.status !== "open" ? "finding-reviewed" : ""}`}>
      <header className="finding-head">
        <span className={`sev sev-${f.severity}`}>{SEVERITY_LABEL[f.severity]}</span>
        <code className="rule-id" title={f.rule_name}>
          {f.rule_id}
        </code>
        <h3>{f.title}</h3>
        {f.model_version && (
          <span className="tag tag-ai" title={`Raised or refined by the AI reviewer (${f.model_version}). Check the evidence before acting on it.`}>
            AI
          </span>
        )}
        {f.status !== "open" && <span className={`tag review-${f.status}`}>{f.status}</span>}
      </header>
      <p className="finding-text">{f.explanation}</p>
      {(atStake || deduction || f.needs_medical_review) && (
        <p className="finding-meta">
          {atStake && <span>At stake {atStake}</span>}
          {deduction && <span>Suggested deduction {deduction}</span>}
          {f.needs_medical_review && <span className="tag">needs medical review</span>}
        </p>
      )}
      {f.evidence.length > 0 && (
        <ul className="evidence">
          {f.evidence.map((e, i) => (
            <li key={i}>
              {e.page_id ? (
                <button className="link-button" onClick={() => onShow(e.page_id as string, e.bbox)} title="Show on the page">
                  ⌕ {e.quote}
                </button>
              ) : (
                <span className="muted">{e.quote}</span>
              )}
              {e.document && <span className="muted evidence-doc"> · {e.document.split(":")[0].replace(/_/g, " ")}</span>}
            </li>
          ))}
        </ul>
      )}
      {f.reviews.length > 0 && (
        <ul className="review-history">
          {f.reviews.map((r) => (
            <li key={r.id}>
              <strong>{r.action === "confirmed" ? "Confirmed" : "Dismissed"}</strong> by {r.reviewer ?? "—"},{" "}
              {when(r.created_at)}
              {r.reason ? `: ${r.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
      {canReview && f.is_current && <ReviewBox finding={f} onDone={onReviewed} />}
    </article>
  );
}

function CheckList({ checks }: { checks: CheckRunRow[] }) {
  const pending = checks.filter((c) => c.status === "pending_ai");
  const rest = checks.filter((c) => c.status !== "pending_ai");
  const row = (c: CheckRunRow) => (
    <tr key={c.rule_id}>
      <td>
        <code>{c.rule_id}</code>
      </td>
      <td>{c.name}</td>
      <td>
        <span className={`check-status status-${c.status}`}>{CHECK_STATUS_LABELS[c.status]}</span>
      </td>
      <td className="muted">{c.detail ?? ""}</td>
    </tr>
  );
  return (
    <div className="table-card">
      <table className="table checks-table">
        <thead>
          <tr>
            <th>Check</th>
            <th>What it looks at</th>
            <th>Result</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>{rest.map(row)}</tbody>
        {pending.length > 0 && (
          <tbody>
            <tr>
              <td colSpan={4} className="checks-group">
                Not run yet: AI checks (Step 10)
              </td>
            </tr>
            {pending.map(row)}
          </tbody>
        )}
      </table>
    </div>
  );
}

export default function FindingsView({ claimId, canReview, onShow, refreshKey }: Props) {
  const [data, setData] = useState<FindingsResponse | null>(null);
  const [checks, setChecks] = useState<ChecksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"open" | "all">("all");
  const [showChecks, setShowChecks] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [f, c] = await Promise.all([
        api.get<FindingsResponse>(`/claims/${claimId}/findings`),
        api.get<ChecksResponse>(`/claims/${claimId}/checks`),
      ]);
      setData(f);
      setChecks(c);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the findings");
    }
  }, [claimId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const rerun = async () => {
    setBusy(true);
    try {
      setChecks(await api.post<ChecksResponse>(`/claims/${claimId}/run-checks`));
      setData(await api.get<FindingsResponse>(`/claims/${claimId}/findings`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run the checks");
    } finally {
      setBusy(false);
    }
  };

  const replace = (updated: Finding) =>
    setData((d) => (d ? { ...d, findings: d.findings.map((f) => (f.id === updated.id ? updated : f)) } : d));

  const current = useMemo(() => (data?.findings ?? []).filter((f) => f.is_current), [data]);
  const history = useMemo(() => (data?.findings ?? []).filter((f) => !f.is_current), [data]);
  const shown = filter === "open" ? current.filter((f) => f.status === "open") : current;

  if (error && !data) return <p className="error-text" role="alert">{error}</p>;
  if (!data || !checks) return <p className="muted">Loading…</p>;
  if (!checks.ran_at) {
    return <p className="muted card">Checks have not run on this claim yet. They run when processing finishes.</p>;
  }

  const s = data.summary;
  return (
    <div className="findings">
      <div className="card findings-summary">
        <div className="sev-counts">
          <span className="sev sev-red">{s.red} red</span>
          <span className="sev sev-amber">{s.amber} amber</span>
          <span className="sev sev-info">{s.info} info</span>
          <span className="muted">{s.open} open</span>
        </div>
        <div className="money-summary">
          {money(s.amount_at_stake) && (
            <span>
              At stake <strong>{money(s.amount_at_stake)}</strong>
            </span>
          )}
          {money(s.suggested_deduction) && (
            <span>
              Suggested deductions <strong>{money(s.suggested_deduction)}</strong>
            </span>
          )}
        </div>
        <div className="head-actions">
          <div className="chips">
            <button className={`chip ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>
              All <span>{current.length}</span>
            </button>
            <button className={`chip ${filter === "open" ? "active" : ""}`} onClick={() => setFilter("open")}>
              Open <span>{s.open}</span>
            </button>
          </div>
          {canReview && (
            <button className="button button-quiet" onClick={rerun} disabled={busy} title="Run the checks again on the claim record, e.g. after corrections">
              {busy ? "Running…" : "Re-run checks"}
            </button>
          )}
        </div>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}

      {current.length === 0 ? (
        <p className="card notice-info">No findings. Every check that could run on this claim passed.</p>
      ) : shown.length === 0 ? (
        <p className="muted">Every finding has been reviewed.</p>
      ) : (
        LAYERS.filter((layer) => shown.some((f) => f.layer === layer)).map((layer) => (
          <section key={layer} className="finding-group">
            <h2 className="section-title">{LAYER_LABELS[layer] ?? layer}</h2>
            {shown
              .filter((f) => f.layer === layer)
              .map((f) => (
                <FindingCard key={f.id} f={f} canReview={canReview} onShow={onShow} onReviewed={replace} />
              ))}
          </section>
        ))
      )}

      {history.length > 0 && (
        <details className="finding-group">
          <summary className="muted">No longer raised ({history.length}, kept because they were reviewed)</summary>
          {history.map((f) => (
            <FindingCard key={f.id} f={f} canReview={false} onShow={onShow} onReviewed={replace} />
          ))}
        </details>
      )}

      <div className="checks-toggle">
        <button className="link-button" onClick={() => setShowChecks((x) => !x)}>
          {showChecks ? "Hide" : "Show"} all {checks.checks.length} checks
        </button>
        <span className="muted">
          {" "}
          {(["passed", "flagged", "not_applicable", "not_evaluable", "pending_ai"] as const)
            .filter((k) => checks.counts[k])
            .map((k) => `${checks.counts[k]} ${k === "pending_ai" ? "waiting for the AI step" : CHECK_STATUS_LABELS[k].toLowerCase()}`)
            .join(" · ")}
        </span>
      </div>
      {showChecks && <CheckList checks={checks.checks} />}
    </div>
  );
}
