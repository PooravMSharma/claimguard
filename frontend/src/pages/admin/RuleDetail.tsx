import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../../api/client";
import { LAYER_LABELS, RULE_STATUS_LABELS, type RuleDetail as Detail, type RuleStatus, type Severity } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Field, clean, type Json } from "../../components/ValueEditor";

const SEVERITIES: Severity[] = ["red", "amber", "info"];
const RANK: Record<Severity, number> = { info: 0, amber: 1, red: 2 };
const when = (v: string) => new Date(v).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
const show = (v: unknown) => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

/** Which settings differ between two config objects (for the history list). */
function configDiff(before: Record<string, unknown>, after: Record<string, unknown>, prefix = ""): string[] {
  return Object.keys({ ...before, ...after })
    .filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
    .flatMap((k) => {
      const name = `${prefix}${k.replace(/_/g, " ")}`;
      const [b, a] = [before[k], after[k]];
      const isObj = (x: unknown) => !!x && typeof x === "object" && !Array.isArray(x);
      if (isObj(b) && isObj(a)) return configDiff(b as Record<string, unknown>, a as Record<string, unknown>, `${name} › `);
      if (Array.isArray(b) && Array.isArray(a) && [...b, ...a].every((x) => typeof x === "string")) {
        const added = a.filter((x) => !b.includes(x));
        const removed = b.filter((x) => !a.includes(x));
        return [`${name}: ${[added.length ? `added ${added.join(", ")}` : "", removed.length ? `removed ${removed.join(", ")}` : ""].filter(Boolean).join("; ")}`];
      }
      return [`${name}: ${show(b)} → ${show(a)}`];
    });
}

export default function RuleDetail() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const canEdit = can("rules.manage");
  const [rule, setRule] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // form
  const [status, setStatus] = useState<RuleStatus>("active");
  const [weight, setWeight] = useState(0);
  const [severity, setSeverity] = useState<Severity>("amber");
  const [config, setConfig] = useState<Record<string, Json>>({});
  const [sourceTitle, setSourceTitle] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [clinician, setClinician] = useState("");
  const [reason, setReason] = useState("");

  const fill = (r: Detail) => {
    setRule(r);
    setStatus(r.status);
    setWeight(r.weight);
    setSeverity(r.max_severity);
    setConfig(r.config as Record<string, Json>);
    setSourceTitle(r.source_title ?? "");
    setSourceUrl(r.source_url ?? "");
    setClinician("");
    setReason("");
  };

  const load = useCallback(async () => {
    try {
      fill(await api.get<Detail>(`/rules/${id}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the rule");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (error && !rule) return <p className="error-text" role="alert">{error}</p>;
  if (!rule) return <p className="muted">Loading…</p>;

  const configChanged = JSON.stringify(clean(config)) !== JSON.stringify(rule.config);
  const dirty =
    status !== rule.status ||
    weight !== rule.weight ||
    severity !== rule.max_severity ||
    configChanged ||
    sourceTitle !== (rule.source_title ?? "") ||
    sourceUrl !== (rule.source_url ?? "");
  const needsClinician = status === "clinician_reviewed" && rule.status !== "clinician_reviewed";

  const save = async () => {
    setBusy(true);
    setSaved(null);
    try {
      const body: Record<string, unknown> = { reason: reason.trim() };
      if (status !== rule.status) body.status = status;
      if (weight !== rule.weight) body.weight = weight;
      if (severity !== rule.max_severity) body.max_severity = severity;
      if (configChanged) body.config = clean(config);
      if (sourceTitle !== (rule.source_title ?? "")) body.source_title = sourceTitle;
      if (sourceUrl !== (rule.source_url ?? "")) body.source_url = sourceUrl;
      if (needsClinician) body.clinician = clinician.trim();
      fill(await api.patch<Detail>(`/rules/${rule.id}`, body));
      setSaved("Saved. The change applies from the next checks run; claims in review were re-scored.");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the change");
    } finally {
      setBusy(false);
    }
  };

  const s = rule.stats;
  const hasConfig = Object.keys(rule.default_config).length > 0;

  return (
    <section>
      <p className="crumbs">
        <Link to="/admin/rules">Rule library</Link> /
      </p>
      <div className="page-head">
        <h1 className="page-title">
          <code className="title-code">{rule.id}</code> {rule.name}
        </h1>
        <span className="tag">v{rule.version}</span>
      </div>

      <div className="card">
        <dl className="facts">
          <div>
            <dt>Layer</dt>
            <dd>{LAYER_LABELS[rule.layer] ?? rule.layer}</dd>
          </div>
          <div>
            <dt>Method</dt>
            <dd>{rule.method}</dd>
          </div>
          <div>
            <dt>Designed severity</dt>
            <dd>
              <span className={`sev sev-${rule.designed_max_severity}`}>{rule.designed_max_severity}</span>
            </dd>
          </div>
          <div>
            <dt>Findings ({s.days} days)</dt>
            <dd>
              {s.findings} raised · {s.reviewed} reviewed
              {s.reviewed > 0 && (
                <div className="small">
                  {s.confirmed} confirmed, {s.dismissed} dismissed ({Math.round((s.dismissal_rate ?? 0) * 100)}%)
                  {s.needs_retuning && <span className="tag tag-bad">retune</span>}
                </div>
              )}
            </dd>
          </div>
          <div className="facts-wide">
            <dt>What it checks</dt>
            <dd>{rule.description}</dd>
          </div>
          <div className="facts-wide">
            <dt>Source</dt>
            <dd>
              {rule.source_url ? (
                <a href={rule.source_url} target="_blank" rel="noreferrer">
                  {rule.source_title || rule.source_url}
                </a>
              ) : (
                rule.source_title || <span className="muted">Not recorded yet</span>
              )}
            </dd>
          </div>
        </dl>
      </div>

      <section className="card rule-form">
        <h2 className="section-title">Settings</h2>
        {!canEdit && <p className="muted small">Read-only: an admin changes rule settings.</p>}
        <div className="form-grid">
          <label>
            Status
            <select value={status} disabled={!canEdit} onChange={(e) => setStatus(e.target.value as RuleStatus)}>
              {(rule.allowed_statuses.includes(rule.status) ? rule.allowed_statuses : [rule.status, ...rule.allowed_statuses]).map((st) => (
                <option key={st} value={st}>
                  {RULE_STATUS_LABELS[st]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Weight (score points, 0–100)
            <input
              type="number"
              min={0}
              max={100}
              value={weight}
              disabled={!canEdit}
              onChange={(e) => setWeight(Math.round(Number(e.target.value)))}
            />
          </label>
          <label>
            Highest severity
            <select value={severity} disabled={!canEdit} onChange={(e) => setSeverity(e.target.value as Severity)}>
              {SEVERITIES.filter((x) => RANK[x] <= RANK[rule.designed_max_severity]).map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
          {needsClinician && (
            <label>
              Reviewing clinician (name, qualification)
              <input value={clinician} onChange={(e) => setClinician(e.target.value)} placeholder="Dr. …, MD (Medicine)" />
            </label>
          )}
        </div>
        {rule.layer === "medical" && (
          <p className="muted small">
            Medical-necessity rules stay amber and marked for medical review until a clinician has reviewed them.
          </p>
        )}

        {hasConfig && (
          <>
            <h3 className="sub-title">
              Thresholds{" "}
              {canEdit && (
                <button
                  type="button"
                  className="link-button small"
                  onClick={() => setConfig(rule.default_config as Record<string, Json>)}
                >
                  Reset to defaults
                </button>
              )}
            </h3>
            <div className="value-group">
              {Object.entries(rule.default_config).map(([k, d]) => (
                <label key={k} className="value-field">
                  <span>
                    {k.replace(/_/g, " ")}
                    {JSON.stringify(config[k]) !== JSON.stringify(d) && <span className="tag">changed</span>}
                  </span>
                  <Field
                    name={k}
                    value={config[k] as Json}
                    shape={d as Json}
                    readOnly={!canEdit}
                    onChange={(v) => setConfig({ ...config, [k]: v })}
                  />
                </label>
              ))}
            </div>
          </>
        )}

        <div className="form-grid">
          <label>
            Source document
            <input value={sourceTitle} disabled={!canEdit} onChange={(e) => setSourceTitle(e.target.value)} placeholder="e.g. IRDAI Master Circular on Health Insurance, 2024" />
          </label>
          <label>
            Source link
            <input value={sourceUrl} disabled={!canEdit} onChange={(e) => setSourceUrl(e.target.value)} placeholder="https://…" />
          </label>
        </div>

        {canEdit && (
          <>
            <label className="block">
              Reason for the change (kept in the rule's history)
              <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            {error && <p className="error-text" role="alert">{error}</p>}
            {saved && <p className="notice-info small">{saved}</p>}
            <div className="finding-actions">
              <button
                className="button"
                onClick={save}
                disabled={busy || !dirty || reason.trim().length < 5 || (needsClinician && !clinician.trim())}
              >
                {busy ? "Saving…" : "Save change"}
              </button>
              {dirty && (
                <button className="link-button" onClick={() => fill(rule)}>
                  Discard
                </button>
              )}
            </div>
          </>
        )}
      </section>

      <div className="two-col">
        <section className="card">
          <h2 className="section-title">History</h2>
          {rule.history.length === 0 ? (
            <p className="muted">Not changed since it was created (v1, default settings).</p>
          ) : (
            <ul className="decision-history">
              {rule.history.map((h) => (
                <li key={h.version}>
                  <strong>v{h.version}</strong> · {h.changed_by ?? "—"}, {when(h.created_at)}
                  <ul className="small">
                    {Object.entries(h.changes).flatMap(([field, [before, after]]) =>
                      field === "config"
                        ? configDiff(before as Record<string, unknown>, after as Record<string, unknown>).map((t) => <li key={t}>{t}</li>)
                        : [
                            <li key={field}>
                              {field.replace(/_/g, " ")}: {show(before)} → {show(after)}
                            </li>,
                          ],
                    )}
                  </ul>
                  <div className="muted">
                    {h.reason}
                    {h.clinician && ` · reviewed by ${h.clinician}`}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card">
          <h2 className="section-title">Why reviewers dismissed it</h2>
          {rule.dismissals.length === 0 ? (
            <p className="muted">No dismissals yet.</p>
          ) : (
            <ul className="decision-history">
              {rule.dismissals.map((d, i) => (
                <li key={i}>
                  {d.reason || <span className="muted">No reason given</span>}
                  <div className="muted small">
                    {d.claim_number} · {when(d.created_at)}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}
