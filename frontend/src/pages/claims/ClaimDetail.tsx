import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { api } from "../../api/client";
import { STAGE_LABELS, type ClaimDetail as Detail } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ACTIVE, StatusTag, fileSize, seconds, when } from "./common";

interface FlatPage {
  id: string;
  fileId: string;
  filename: string;
  pageNumber: number;
  hasText: boolean;
}

export default function ClaimDetail() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const { can } = useAuth();
  const [claim, setClaim] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
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

  const pages: FlatPage[] = useMemo(
    () =>
      (claim?.files ?? []).flatMap((f) =>
        f.pages.map((p) => ({ id: p.id, fileId: f.id, filename: f.filename, pageNumber: p.page_number, hasText: p.has_text_layer })),
      ),
    [claim],
  );
  const current = pages[Math.min(selected, Math.max(pages.length - 1, 0))];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "ArrowRight") setSelected((s) => Math.min(s + 1, pages.length - 1));
      if (e.key === "ArrowLeft") setSelected((s) => Math.max(s - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pages.length]);

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

      {pages.length > 0 && current && (
        <div className="viewer">
          <div className="thumbs" aria-label="Pages">
            {pages.map((p, i) => (
              <button
                key={p.id}
                className={i === selected ? "thumb active" : "thumb"}
                onClick={() => setSelected(i)}
                title={`${p.filename}, page ${p.pageNumber}`}
              >
                <img src={`/api/claims/${claim.id}/pages/${p.id}/image`} alt="" loading="lazy" />
                <span>{i + 1}</span>
              </button>
            ))}
          </div>
          <div className="page-view">
            <div className="page-bar">
              <button className="button button-quiet" disabled={selected === 0} onClick={() => setSelected(selected - 1)}>
                ‹ Prev
              </button>
              <span className="muted">
                Page {selected + 1} of {pages.length} · {current.filename}, p. {current.pageNumber}
                {current.hasText ? " · has text layer" : " · image only (needs OCR)"}
              </span>
              <button
                className="button button-quiet"
                disabled={selected >= pages.length - 1}
                onClick={() => setSelected(selected + 1)}
              >
                Next ›
              </button>
            </div>
            <img className="page-img" src={`/api/claims/${claim.id}/pages/${current.id}/image`} alt={`Page ${selected + 1}`} />
          </div>
        </div>
      )}
    </section>
  );
}