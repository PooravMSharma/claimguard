import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import {
  STAGE_LABELS,
  STATUS_LABELS,
  WAITING_LABELS,
  type ClaimList,
  type ClaimStatus,
  type RiskBand,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ACTIVE, BandTag, StatusTag, TimeLeft, money, useNow, when } from "./common";

const PAGE_SIZE = 50;
const STATUSES = Object.keys(STATUS_LABELS) as ClaimStatus[];
const BANDS: RiskBand[] = ["red", "amber", "green"];
const SORTS = {
  priority: "Priority (band, score, deadline)",
  deadline: "Deadline",
  score: "Risk score",
  newest: "Newest first",
} as const;
type Sort = keyof typeof SORTS;

export default function ClaimQueue() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const now = useNow();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<ClaimStatus | "">("");
  const [band, setBand] = useState<RiskBand | "">("");
  const [mine, setMine] = useState(false);
  const [sort, setSort] = useState<Sort>("priority");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<ClaimList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), sort });
    if (q.trim()) params.set("q", q.trim());
    if (status) params.set("status", status);
    if (band) params.set("band", band);
    if (mine) params.set("assignee", "me");
    try {
      setData(await api.get<ClaimList>(`/claims?${params.toString()}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load claims");
    }
  }, [q, status, band, mine, sort, offset]);

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
  const reset = () => setOffset(0);

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
        <button className={status === "" ? "chip active" : "chip"} onClick={() => { setStatus(""); reset(); }}>
          All <span>{allCount}</span>
        </button>
        {STATUSES.map((s) => (
          <button key={s} className={status === s ? "chip active" : "chip"} onClick={() => { setStatus(s); reset(); }}>
            {STATUS_LABELS[s]} <span>{data?.counts[s] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="toolbar">
        <input
          placeholder="Search claim, policy, patient, hospital"
          value={q}
          onChange={(e) => { setQ(e.target.value); reset(); }}
          aria-label="Search claims"
        />
        <div className="chips" role="group" aria-label="Filter by risk band">
          {BANDS.map((b) => (
            <button
              key={b}
              className={`chip chip-band-${b} ${band === b ? "active" : ""}`}
              aria-pressed={band === b}
              onClick={() => { setBand(band === b ? "" : b); reset(); }}
            >
              {b[0].toUpperCase() + b.slice(1)} <span>{data?.band_counts?.[b] ?? 0}</span>
            </button>
          ))}
        </div>
        {can("claims.review") && (
          <label className="check small">
            <input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); reset(); }} /> Assigned
            to me
          </label>
        )}
        <label className="small sort-select">
          Sort{" "}
          <select value={sort} onChange={(e) => { setSort(e.target.value as Sort); reset(); }}>
            {(Object.keys(SORTS) as Sort[]).map((s) => (
              <option key={s} value={s}>
                {SORTS[s]}
              </option>
            ))}
          </select>
        </label>
        {busy && <span className="muted">Processing… this list refreshes on its own.</span>}
      </div>

      {error && <p className="error-text" role="alert">{error}</p>}

      <div className="card table-card">
        <table className="table queue-table">
          <thead>
            <tr>
              <th>Risk</th>
              <th>Claim</th>
              <th>Patient · hospital</th>
              <th className="num">Claimed</th>
              <th>Status</th>
              <th className="nowrap">Time left</th>
              <th>Assigned</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((c) => {
              const waiting = c.status === "in_review" && c.latest_recommendation
                ? WAITING_LABELS[c.latest_recommendation]
                : undefined;
              return (
                <tr key={c.id} className="clickable" onClick={() => navigate(`/claims/${c.id}`)}>
                  <td>
                    <BandTag band={c.risk_band} score={c.risk_score} />
                  </td>
                  <td className="nowrap">
                    <Link to={`/claims/${c.id}`} onClick={(e) => e.stopPropagation()}>
                      <code>{c.claim_number}</code>
                    </Link>
                    {c.source === "synthetic" && <span className="tag">test</span>}
                    <div className="muted small" title={`Received ${when(c.created_at)}`}>
                      {STAGE_LABELS[c.stage]}
                    </div>
                  </td>
                  <td>
                    {c.member_name ?? <span className="muted">—</span>}
                    {c.hospital_name && <div className="muted small clamp">{c.hospital_name}</div>}
                  </td>
                  <td className="num nowrap">{money(c.claimed_amount, false) ?? <span className="muted">—</span>}</td>
                  <td>
                    <StatusTag status={c.status} />
                    {waiting && <div className="small waiting">{waiting}</div>}
                    {c.status === "failed" && c.processing_error && (
                      <div className="error-text small clamp">{c.processing_error}</div>
                    )}
                  </td>
                  <td className="nowrap">
                    <TimeLeft due={c.sla_due_at} done={c.status === "decided"} now={now} />
                  </td>
                  <td className="nowrap">{c.assignee ?? <span className="muted">—</span>}</td>
                </tr>
              );
            })}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  {q || status || band || mine ? "No claims match." : "No claims yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {total > PAGE_SIZE && (
        <div className="pager">
          <button className="button button-quiet" disabled={offset === 0} onClick={() => setOffset(offset - PAGE_SIZE)}>
            Previous
          </button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button
            className="button button-quiet"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      )}
    </section>
  );
}
