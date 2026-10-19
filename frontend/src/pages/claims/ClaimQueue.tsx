import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import { STAGE_LABELS, STATUS_LABELS, type ClaimList, type ClaimStatus } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ACTIVE, StatusTag, when } from "./common";

const PAGE_SIZE = 50;
const STATUSES = Object.keys(STATUS_LABELS) as ClaimStatus[];

export default function ClaimQueue() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<ClaimStatus | "">("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<ClaimList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (q.trim()) params.set("q", q.trim());
    if (status) params.set("status", status);
    try {
      setData(await api.get<ClaimList>(`/claims?${params.toString()}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load claims");
    }
  }, [q, status, offset]);

  useEffect(() => {
    const timer = setTimeout(load, 200);
    return () => clearTimeout(timer);
  }, [load]);

  // Keep refreshing while anything is still being processed.
  const busy = !!data && ACTIVE.some((s) => (data.counts[s] ?? 0) > 0);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [busy, load]);

  const total = data?.total ?? 0;
  const allCount = Object.values(data?.counts ?? {}).reduce((a, b) => a + (b ?? 0), 0);

  return (
    <section>
      <div className="page-head">
        <h1 className="page-title">Claim queue</h1>
        {can("claims.upload") && (
          <Link className="button" to="/upload">
            Upload claim
          </Link>
        )}
      </div>

      <div className="status-filters" role="tablist" aria-label="Filter by status">
        <button className={status === "" ? "chip active" : "chip"} onClick={() => { setStatus(""); setOffset(0); }}>
          All <span>{allCount}</span>
        </button>
        {STATUSES.map((s) => (
          <button
            key={s}
            className={status === s ? "chip active" : "chip"}
            onClick={() => { setStatus(s); setOffset(0); }}
          >
            {STATUS_LABELS[s]} <span>{data?.counts[s] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="toolbar">
        <input
          placeholder="Search claim no., policy no. or patient"
          value={q}
          onChange={(e) => { setQ(e.target.value); setOffset(0); }}
          aria-label="Search claims"
        />
        {busy && <span className="muted">Processing… this list refreshes on its own.</span>}
      </div>

      {error && <p className="error-text" role="alert">{error}</p>}

      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Patient</th>
              <th>Stage</th>
              <th>Status</th>
              <th>Files</th>
              <th>Received</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((c) => (
              <tr key={c.id} className="clickable" onClick={() => navigate(`/claims/${c.id}`)}>
                <td className="nowrap">
                  <Link to={`/claims/${c.id}`} onClick={(e) => e.stopPropagation()}>
                    <code>{c.claim_number}</code>
                  </Link>
                  {c.source === "synthetic" && <span className="tag">test</span>}
                </td>
                <td>
                  {c.member_name ?? <span className="muted">—</span>}
                  {c.policy_number && <div className="muted small">{c.policy_number}</div>}
                </td>
                <td className="nowrap">{STAGE_LABELS[c.stage]}</td>
                <td>
                  <StatusTag status={c.status} />
                  {c.status === "failed" && c.processing_error && (
                    <div className="error-text small clamp">{c.processing_error}</div>
                  )}
                </td>
                <td className="nowrap muted">
                  {c.documents} file{c.documents === 1 ? "" : "s"} · {c.pages} pages
                </td>
                <td className="nowrap">{when(c.created_at)}</td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  {q || status ? "No claims match." : "No claims yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {total > PAGE_SIZE && (
        <div className="pager">
          <button className="button button-quiet" disabled={offset === 0} onClick={() => setOffset(offset - PAGE_SIZE)}>
            Newer
          </button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button
            className="button button-quiet"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Older
          </button>
        </div>
      )}
    </section>
  );
}