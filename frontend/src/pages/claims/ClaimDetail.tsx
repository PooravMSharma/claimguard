import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { api } from "../../api/client";
import { DOC_TYPE_LABELS, STAGE_LABELS, type Bbox, type ClaimDetail as Detail, type Focus } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ACTIVE, StatusTag, fileSize, seconds, when } from "./common";
import ClaimRecordView from "./ClaimRecordView";
import FindingsView from "./FindingsView";
import PageViewer from "./PageViewer";

export default function ClaimDetail() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const { can } = useAuth();
  const [claim, setClaim] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"findings" | "record" | "pages">("findings");
  const [focus, setFocus] = useState<Focus | null>(null);
  const showOnPage = (pageId: string, bbox: Bbox | null) => {
    setFocus({ pageId, bbox, stamp: Date.now() });
    setTab("pages");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const warnings = (location.state as { warnings?: string[] } | null)?.warnings ?? [];

  const load = useCallback(async () => {
    try {
      setClaim(await api.get<Detail>(`/claims/${id}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the claim");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const active = !!claim && ACTIVE.includes(claim.status);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(load, 2500);
    return () => clearInterval(timer);
  }, [active, load]);

  const reprocess = async () => {
    setBusy(true);
    try {
      setClaim(await api.post<Detail>(`/claims/${id}/reprocess`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not restart processing");
    } finally {
      setBusy(false);
    }
  };

  if (error && !claim) return <p className="error-text" role="alert">{error}</p>;
  if (!claim) return <p className="muted">Loading…</p>;

  const took = seconds(claim.processing_started_at, claim.processing_finished_at);

  return (
    <section>
      <p className="crumbs">
        <Link to="/claims">Claim queue</Link> /
      </p>
      <div className="page-head">
        <h1 className="page-title">
          <code className="title-code">{claim.claim_number}</code>
        </h1>
        <div className="head-actions">
          <StatusTag status={claim.status} />
          {can("claims.upload") && (claim.status === "failed" || claim.status === "ready_for_review") && (
            <button className="button button-quiet" onClick={reprocess} disabled={busy}>
              {busy ? "Restarting…" : "Reprocess"}
            </button>
          )}
        </div>
      </div>

      {warnings.map((w) => (
        <p key={w} className="notice-warn">
          {w}
        </p>
      ))}
      {error && <p className="error-text" role="alert">{error}</p>}
      {claim.status === "failed" && (
        <div className="card notice-bad">
          <strong>Processing failed.</strong> {claim.processing_error}
        </div>
      )}
      {claim.status === "ready_for_review" && claim.pages_needing_review > 0 && (
        <p className="notice-warn">
          {claim.pages_needing_review} page{claim.pages_needing_review === 1 ? " is" : "s are"} a poor scan. Values read
          from {claim.pages_needing_review === 1 ? "it" : "them"} will be marked for confirmation.
        </p>
      )}
      {active && (
        <div className="card notice-info">
          <strong>{claim.status === "received" ? "Waiting in the queue…" : "Processing…"}</strong> Pages appear here
          when the files have been split. This page updates on its own.
        </div>
      )}

      <div className="card">
        <dl className="facts">
          <div>
            <dt>Stage</dt>
            <dd>{STAGE_LABELS[claim.stage]}</dd>
          </div>
          <div>
            <dt>Patient</dt>
            <dd>{claim.member_name ?? "—"}</dd>
          </div>
          <div>
            <dt>Policy no.</dt>
            <dd>{claim.policy_number ?? "—"}</dd>
          </div>
          <div>
            <dt>Received</dt>
            <dd>
              {when(claim.created_at)}
              {claim.created_by ? ` by ${claim.created_by}` : claim.source === "synthetic" ? " (test claim)" : ""}
            </dd>
          </div>
          <div>
            <dt>Files</dt>
            <dd>
              {claim.documents} · {claim.pages} pages
            </dd>
          </div>
          <div className="facts-wide">
            <dt>Documents found</dt>
            <dd className="type-chips">
              {Object.entries(claim.page_types).length === 0
                ? "—"
                : Object.entries(claim.page_types)
                    .sort((a, b) => b[1] - a[1])
                    .map(([t, n]) => (
                      <span key={t} className={`tag ${t === "unknown" ? "tag-bad" : ""}`}>
                        {DOC_TYPE_LABELS[t] ?? t}
                        {n > 1 ? ` ×${n}` : ""}
                      </span>
                    ))}
            </dd>
          </div>
          <div>
            <dt>Processing</dt>
            <dd>{took ? `Done in ${took}` : claim.processing_started_at ? "Running" : "Not started"}</dd>
          </div>
        </dl>
      </div>

      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>File</th>
              <th>Type</th>
              <th>Size</th>
              <th>Pages</th>
              <th>SHA-256</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {claim.files.map((f) => (
              <tr key={f.id}>
                <td>
                  {f.filename}
                  {f.duplicate_of_id && <span className="tag tag-bad">same file seen before</span>}
                </td>
                <td className="muted">{f.content_type.split("/")[1].toUpperCase()}</td>
                <td className="nowrap">{fileSize(f.size_bytes)}</td>
                <td>{f.page_count ?? "—"}</td>
                <td>
                  <code title={f.sha256}>{f.sha256.slice(0, 12)}…</code>
                </td>
                <td className="row-actions">
                  <a className="link-button" href={`/api/claims/${claim.id}/documents/${f.id}/original`} target="_blank" rel="noreferrer">
                    Open original
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "findings"} className={tab === "findings" ? "tab active" : "tab"} onClick={() => setTab("findings")}>
          Findings
        </button>
        <button role="tab" aria-selected={tab === "record"} className={tab === "record" ? "tab active" : "tab"} onClick={() => setTab("record")}>
          Claim record
        </button>
        <button role="tab" aria-selected={tab === "pages"} className={tab === "pages" ? "tab active" : "tab"} onClick={() => setTab("pages")}>
          Documents ({claim.pages} pages)
        </button>
      </div>
      {tab === "findings" ? (
        <FindingsView
          claimId={claim.id}
          canReview={can("claims.review") || can("claims.investigate")}
          onShow={showOnPage}
          refreshKey={`${claim.status}-${claim.processing_finished_at ?? ""}`}
        />
      ) : tab === "record" ? (
        <ClaimRecordView
          claimId={claim.id}
          canCorrect={can("claims.review")}
          onShow={showOnPage}
          refreshKey={`${claim.status}-${claim.processing_finished_at ?? ""}`}
        />
      ) : (
        <PageViewer claim={claim} canEditType={can("claims.review")} onChanged={setClaim} focus={focus} />
      )}
    </section>
  );
}
