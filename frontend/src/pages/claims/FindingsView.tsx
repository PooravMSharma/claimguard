import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
import { money } from "./common";

interface Props {
  claimId: string;
  canReview: boolean;
  canSettleMedical: boolean;           // senior / medical reviewer: confirms or dismisses medical findings
  locked: boolean;                     // claim decided: no more reviews
  onShow: (pageId: string, bbox: Bbox | null, jump?: boolean) => void;   // jump: the reviewer asked to see it
  onChanged?: () => void;              // a review or re-run changed the findings (score, blockers)
  refreshKey: string;
}

const SEVERITY_LABEL = { red: "Red", amber: "Amber", info: "Info" } as const;
const SEVERITY_RANK = { red: 0, amber: 1, info: 2 } as const;
const ACTION_LABEL = { confirmed: "Confirmed", dismissed: "Dismissed", noted: "Note" } as const;

const when = (v: string) => new Date(v).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

/** Ranked as the reviewer should work through them: open first, then severity, then money at stake. */
function rank(a: Finding, b: Finding): number {
  const open = Number(a.status !== "open") - Number(b.status !== "open");
  if (open) return open;
  const sev = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
  if (sev) return sev;
  return Number(b.amount_at_stake ?? 0) - Number(a.amount_at_stake ?? 0) || a.rule_id.localeCompare(b.rule_id);
}

type Mode = "confirmed" | "dismissed" | "noted";

function ReviewBox({ finding, mode, setMode, canSettle, onDone }: {
  finding: Finding;
  mode: Mode | null;
  setMode: (m: Mode | null) => void;
  canSettle: boolean;
  onDone: (f: Finding) => void;
}) {
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
        {canSettle ? (
          <>
            <button className="button button-quiet" onClick={() => setMode("confirmed")} title="Confirm (c)">
              Confirm
            </button>
            <button className="button button-quiet" onClick={() => setMode("dismissed")} title="Dismiss (d)">
              Dismiss
            </button>
          </>
        ) : (
          <span className="muted small">A senior / medical reviewer confirms or dismisses this one.</span>
        )}
        <button className="link-button" onClick={() => setMode("noted")} title="Add a note (n)">
          Add note
        </button>
      </div>
    );
  }
  const needsText = mode !== "confirmed";
  return (
    <div className="review-box">
      <label>
        {mode === "dismissed" ? "Why is this not a problem? (required)" : mode === "noted" ? "Note" : "Note (optional)"}
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Escape") setMode(null);
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !(needsText && !reason.trim())) send();
          }}
        />
      </label>
      {error && <p className="error-text">{error}</p>}
      <div className="finding-actions">
        <button className="button" onClick={send} disabled={busy || (needsText && !reason.trim())}>
          {busy ? "Saving…" : mode === "dismissed" ? "Dismiss finding" : mode === "noted" ? "Save note" : "Confirm finding"}
        </button>
        <button className="link-button" onClick={() => setMode(null)}>
          Cancel
        </button>
        <span className="muted small">Ctrl+Enter to save · Esc to cancel</span>
      </div>
    </div>
  );
}

function FindingCard({ f, selected, canReview, canSettle, mode, setMode, onSelect, onShow, onReviewed }: {
  f: Finding;
  selected: boolean;
  canReview: boolean;
  canSettle: boolean;
  mode: Mode | null;
  setMode: (m: Mode | null) => void;
  onSelect: () => void;
  onShow: Props["onShow"];
  onReviewed: (f: Finding) => void;
}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);
  const atStake = money(f.amount_at_stake);
  const deduction = money(f.suggested_deduction);
  return (
    <article
      ref={ref}
      className={`finding finding-${f.severity} ${f.status !== "open" ? "finding-reviewed" : ""} ${selected ? "finding-selected" : ""}`}
      onClick={onSelect}
      aria-current={selected}
    >
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
      <p className="finding-meta">
        <span className="muted">{LAYER_LABELS[f.layer] ?? f.layer}</span>
        {atStake && <span>At stake {atStake}</span>}
        {deduction && <span>Suggested deduction {deduction}</span>}
        {f.needs_medical_review && <span className="tag">needs medical review</span>}
      </p>
      {f.evidence.length > 0 && (
        <ul className="evidence">
          {f.evidence.map((e, i) => (
            <li key={i}>
              {e.page_id ? (
                <button
                  className="link-button"
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onShow(e.page_id as string, e.bbox, true);
                  }}
                  title="Show on the page"
                >
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
              <strong>{ACTION_LABEL[r.action] ?? r.action}</strong> by {r.reviewer ?? "—"}, {when(r.created_at)}
              {r.reason ? `: ${r.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
      {canReview && f.is_current && (
        <div onClick={(e) => e.stopPropagation()}>
          <ReviewBox finding={f} mode={selected ? mode : null} setMode={(m) => { onSelect(); setMode(m); }} canSettle={canSettle} onDone={onReviewed} />
        </div>
      )}
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
                Waiting for the AI reviewer (no AI model configured, or it was unavailable)
              </td>
            </tr>
            {pending.map(row)}
          </tbody>
        )}
      </table>
    </div>
  );
}

export default function FindingsView({ claimId, canReview, canSettleMedical, locked, onShow, onChanged, refreshKey }: Props) {
  const [data, setData] = useState<FindingsResponse | null>(null);
  const [checks, setChecks] = useState<ChecksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"open" | "all">("all");
  const [showChecks, setShowChecks] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);

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
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run the checks");
    } finally {
      setBusy(false);
    }
  };

  const current = useMemo(() => (data?.findings ?? []).filter((f) => f.is_current).sort(rank), [data]);
  const history = useMemo(() => (data?.findings ?? []).filter((f) => !f.is_current), [data]);
  const shown = filter === "open" ? current.filter((f) => f.status === "open") : current;
  const selectedIndex = shown.findIndex((f) => f.id === selectedId);
  const selected = selectedIndex >= 0 ? shown[selectedIndex] : null;

  const select = useCallback(
    (f: Finding | undefined) => {
      if (!f) return;
      setSelectedId(f.id);
      setMode(null);
      const first = f.evidence.find((e) => e.page_id);
      if (first) onShow(first.page_id as string, first.bbox);
    },
    [onShow],
  );

  const replace = (updated: Finding) => {
    setData((d) => (d ? { ...d, findings: d.findings.map((f) => (f.id === updated.id ? updated : f)) } : d));
    onChanged?.();
    // move on to the next open finding, the way a reviewer works through the list
    if (updated.status !== "open") {
      const next = shown.find((f, i) => i > selectedIndex && f.status === "open" && f.id !== updated.id);
      if (next) select(next);
    }
  };

  // Keyboard: j / k next and previous finding, c confirm, d dismiss, n note, e show the evidence.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j") select(shown[Math.min(selectedIndex + 1, shown.length - 1)]);
      else if (e.key === "k") select(shown[Math.max(selectedIndex - 1, 0)]);
      else if (!selected || !canReview || locked || !selected.is_current) return;
      else if (e.key === "c" || e.key === "d") {
        if (selected.needs_medical_review && !canSettleMedical) return;
        e.preventDefault();
        setMode(e.key === "c" ? "confirmed" : "dismissed");
      } else if (e.key === "n") {
        e.preventDefault();
        setMode("noted");
      } else if (e.key === "e") {
        const first = selected.evidence.find((x) => x.page_id);
        if (first) onShow(first.page_id as string, first.bbox, true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, selectedIndex, selected, canReview, canSettleMedical, locked, onShow, select]);

  if (error && !data) return <p className="error-text" role="alert">{error}</p>;
  if (!data || !checks) return <p className="muted">Loading…</p>;
  if (!checks.ran_at) {
    return <p className="muted card">Checks have not run on this claim yet. They run when processing finishes.</p>;
  }

  const s = data.summary;
  const reviewable = canReview && !locked;
  return (
    <div className="findings">
      <div className="card findings-summary">
        <div className="sev-counts">
          <span className="sev sev-red">{s.red} red</span>
          <span className="sev sev-amber">{s.amber} amber</span>
          <span className="sev sev-info">{s.info} info</span>
          <span className="muted">{s.open} open</span>
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
          {reviewable && (
            <button className="button button-quiet" onClick={rerun} disabled={busy} title="Run the checks again on the claim record, e.g. after corrections">
              {busy ? "Running…" : "Re-run checks"}
            </button>
          )}
        </div>
      </div>
      {reviewable && current.length > 0 && (
        <p className="muted small shortcuts">
          Keys: <kbd>j</kbd>/<kbd>k</kbd> next / previous · <kbd>c</kbd> confirm · <kbd>d</kbd> dismiss · <kbd>n</kbd> note
          · <kbd>e</kbd> show evidence · <kbd>←</kbd>/<kbd>→</kbd> pages
        </p>
      )}
      {error && <p className="error-text" role="alert">{error}</p>}

      {current.length === 0 ? (
        <p className="card notice-info">No findings. Every check that could run on this claim passed.</p>
      ) : shown.length === 0 ? (
        <p className="muted">Every finding has been reviewed.</p>
      ) : (
        <div className="finding-list">
          {shown.map((f) => (
            <FindingCard
              key={f.id}
              f={f}
              selected={f.id === selectedId}
              canReview={reviewable}
              canSettle={!f.needs_medical_review || canSettleMedical}
              mode={mode}
              setMode={setMode}
              onSelect={() => { if (f.id !== selectedId) select(f); }}
              onShow={onShow}
              onReviewed={replace}
            />
          ))}
        </div>
      )}

      {history.length > 0 && (
        <details className="finding-group">
          <summary className="muted">No longer raised ({history.length}, kept because they were reviewed)</summary>
          {history.map((f) => (
            <FindingCard
              key={f.id}
              f={f}
              selected={false}
              canReview={false}
              canSettle={false}
              mode={null}
              setMode={() => undefined}
              onSelect={() => undefined}
              onShow={onShow}
              onReviewed={replace}
            />
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
            .map((k) => `${checks.counts[k]} ${k === "pending_ai" ? "waiting for the AI reviewer" : CHECK_STATUS_LABELS[k].toLowerCase()}`)
            .join(" · ")}
        </span>
      </div>
      {showChecks && <CheckList checks={checks.checks} />}
    </div>
  );
}
