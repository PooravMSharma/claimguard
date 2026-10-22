import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import { LAYER_LABELS, RULE_STATUS_LABELS, type RuleRow, type RuleStatus } from "../../api/types";

const LAYERS = ["completeness", "forensics", "consistency", "medical", "billing", "policy", "registry"];
const METHOD_LABELS: Record<string, string> = {
  rule: "Rule",
  "rule+ai": "Rule + AI",
  "ai+rule": "AI + rule",
  ai: "AI",
  "ai+stg": "AI (STG)",
  benchmark: "Benchmark",
  lookup: "Lookup",
  image_hash: "Image hash",
};

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

export default function Rules() {
  const navigate = useNavigate();
  const [rules, setRules] = useState<RuleRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layer, setLayer] = useState("");
  const [status, setStatus] = useState<RuleStatus | "">("");
  const [retune, setRetune] = useState(false);
  const [q, setQ] = useState("");

  useEffect(() => {
    api
      .get<RuleRow[]>("/rules")
      .then(setRules)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the rules"));
  }, []);

  const shown = useMemo(
    () =>
      (rules ?? []).filter(
        (r) =>
          (!layer || r.layer === layer) &&
          (!status || r.status === status) &&
          (!retune || r.stats.needs_retuning) &&
          (!q.trim() || `${r.id} ${r.name}`.toLowerCase().includes(q.trim().toLowerCase())),
      ),
    [rules, layer, status, retune, q],
  );
  const retuneCount = (rules ?? []).filter((r) => r.stats.needs_retuning).length;

  return (
    <section>
      <div className="page-head">
        <h1 className="page-title">Rule library</h1>
      </div>
      <p className="muted">
        Every check ClaimGuard runs, with its settings and how reviewers judged its findings over the last 90 days.
        A rule whose findings reviewers dismiss half the time or more (with at least 5 reviewed) is marked for
        retuning.
      </p>

      <div className="toolbar">
        <input placeholder="Search rule ID or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search rules" />
        <select value={layer} onChange={(e) => setLayer(e.target.value)} aria-label="Layer">
          <option value="">All layers</option>
          {LAYERS.map((l) => (
            <option key={l} value={l}>
              {LAYER_LABELS[l]}
            </option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value as RuleStatus | "")} aria-label="Status">
          <option value="">Any status</option>
          {(Object.keys(RULE_STATUS_LABELS) as RuleStatus[]).map((s) => (
            <option key={s} value={s}>
              {RULE_STATUS_LABELS[s]}
            </option>
          ))}
        </select>
        <label className="check small">
          <input type="checkbox" checked={retune} onChange={(e) => setRetune(e.target.checked)} /> Needs retuning ({retuneCount})
        </label>
      </div>

      {error && <p className="error-text" role="alert">{error}</p>}
      {!rules && !error && <p className="muted">Loading…</p>}
      {rules && (
        <div className="card table-card">
          <table className="table rules-table">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Layer</th>
                <th>Method</th>
                <th>Status</th>
                <th>Max severity</th>
                <th className="num">Weight</th>
                <th className="num">Findings</th>
                <th className="num">Dismissed</th>
                <th className="num">Version</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id} className={`clickable ${r.status === "disabled" ? "row-muted" : ""}`} onClick={() => navigate(`/admin/rules/${r.id}`)}>
                  <td>
                    <Link to={`/admin/rules/${r.id}`} onClick={(e) => e.stopPropagation()}>
                      <code>{r.id}</code>
                    </Link>{" "}
                    {r.name}
                  </td>
                  <td className="nowrap small">{LAYER_LABELS[r.layer] ?? r.layer}</td>
                  <td className="nowrap small muted">{METHOD_LABELS[r.method] ?? r.method}</td>
                  <td className="nowrap">
                    <span className={`rule-status rule-${r.status}`}>{r.status.replace("_", " ")}</span>
                  </td>
                  <td>
                    <span className={`sev sev-${r.max_severity}`}>{r.max_severity}</span>
                    {r.max_severity !== r.designed_max_severity && <span className="muted small"> (lowered)</span>}
                  </td>
                  <td className="num">{r.weight}</td>
                  <td className="num">{r.stats.findings}</td>
                  <td className="num nowrap">
                    {r.stats.reviewed ? `${pct(r.stats.dismissal_rate)} of ${r.stats.reviewed}` : "—"}
                    {r.stats.needs_retuning && <span className="tag tag-bad">retune</span>}
                  </td>
                  <td className="num">v{r.version}</td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={9} className="muted">
                    No rules match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
