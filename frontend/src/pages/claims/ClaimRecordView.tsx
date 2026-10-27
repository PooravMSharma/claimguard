import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import {
  DOC_TYPE_LABELS,
  FIELD_LABELS,
  type Bbox,
  type ClaimRecord,
  type RecordDocument,
  type RecordField,
} from "../../api/types";

interface Props {
  claimId: string;
  canCorrect: boolean;
  onShow: (pageId: string, bbox: Bbox | null) => void;
  refreshKey: string;
}

const money = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === ""
    ? "—"
    : `₹${Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const dateTime = (v: string | number | null | undefined) =>
  v ? new Date(String(v)).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—";

const day = (v: string | null) => (v ? new Date(v).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "—");

function stayDays(a: unknown, b: unknown): string | null {
  if (!a || !b) return null;
  const days = Math.round((new Date(String(b)).getTime() - new Date(String(a)).getTime()) / 86400000);
  return days <= 0 ? "same day" : `${days} day${days === 1 ? "" : "s"}`;
}

function ShowLink({ pageId, bbox, onShow }: { pageId: string | null; bbox: Bbox | null; onShow: Props["onShow"] }) {
  if (!pageId) return null;
  return (
    <button className="link-button show-link" onClick={() => onShow(pageId, bbox)} title="Show on the page">
      ⌕
    </button>
  );
}

function FieldRow({ f, claimId, canCorrect, onShow, onSaved }: {
  f: RecordField;
  claimId: string;
  canCorrect: boolean;
  onShow: Props["onShow"];
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(f.corrected_value ?? f.value ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    try {
      await api.patch(`/claims/${claimId}/fields/${f.id}`, { value });
      setEditing(false);
      setError(null);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  };

  const shown = f.corrected_value ?? f.value;
  return (
    <tr className={f.needs_review && !f.corrected_value ? "row-review" : ""}>
      <td className="field-name">{FIELD_LABELS[f.name] ?? f.name.replace(/_/g, " ")}</td>
      <td>
        {editing ? (
          <span className="edit-inline">
            <input value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
            <button className="button" onClick={save}>
              Save
            </button>
            <button className="link-button" onClick={() => setEditing(false)}>
              Cancel
            </button>
            {error && <span className="error-text small">{error}</span>}
          </span>
        ) : (
          <>
            <span className="field-value">{shown}</span>
            {f.corrected_value && (
              <span className="muted small" title={`Read as: ${f.value}`}>
                {" "}
                (corrected)
              </span>
            )}
          </>
        )}
      </td>
      <td className="nowrap field-meta">
        {f.corrected_value ? (
          <span className="chip-small chip-reviewer">reviewer</span>
        ) : (
          <span className={`chip-small chip-${f.method}`}>{f.method === "ai" ? "AI" : "rule"}</span>
        )}
        {f.confidence !== null && f.confidence < 1 && !f.corrected_value && (
          <span className={`muted small ${f.confidence < 0.8 ? "low-conf" : ""}`}> {Math.round(f.confidence * 100)}%</span>
        )}
        <ShowLink pageId={f.page_id} bbox={f.bbox} onShow={onShow} />
        {canCorrect && !editing && (
          <button className="link-button" onClick={() => setEditing(true)} title="Correct this value">
            ✎
          </button>
        )}
      </td>
    </tr>
  );
}

function DocumentCard({ doc, ...rest }: { doc: RecordDocument } & Omit<Parameters<typeof FieldRow>[0], "f">) {
  const [open, setOpen] = useState(false);
  const review = doc.fields.filter((f) => f.needs_review && !f.corrected_value).length;
  return (
    <div className="card doc-card">
      <button className="doc-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="doc-title">
          {open ? "▾" : "▸"} {DOC_TYPE_LABELS[doc.doc_type] ?? doc.doc_type}
          {doc.key.includes(":") && <span className="muted"> #{doc.key.split(":")[1]}</span>}
        </span>
        <span className="doc-badges">
          <span className="muted small">{doc.fields.length} values</span>
          {doc.ai_used && <span className="chip-small chip-ai">AI read</span>}
          {doc.quality && doc.quality !== "good" && <span className={`quality-tag q-${doc.quality}`}>{doc.quality}</span>}
          {review > 0 && <span className="tag tag-bad">{review} to check</span>}
          {doc.missing.length > 0 && <span className="tag tag-bad">not found: {doc.missing.join(", ")}</span>}
        </span>
      </button>
      {open && (
        <table className="table field-table">
          <tbody>
            {doc.fields.map((f) => (
              <FieldRow key={f.id} f={f} {...rest} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function ClaimRecordView({ claimId, canCorrect, onShow, refreshKey }: Props) {
  const [record, setRecord] = useState<ClaimRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRecord(await api.get<ClaimRecord>(`/claims/${claimId}/record`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the claim record");
    }
  }, [claimId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (error) return <p className="error-text">{error}</p>;
  if (!record) return <p className="muted">Loading…</p>;
  if (record.documents.length === 0)
    return <div className="card muted">Nothing has been read from this claim yet. It appears here after processing.</div>;

  const s = record.summary;
  const billTotal = record.bill_lines.reduce((sum, b) => sum + Number(b.amount), 0);
  const unreported = record.investigations.filter((i) => i.billed && !i.has_report);
  const notCharted = record.medications.filter((m) => m.in_medication_chart === false);
  const aboveMrp = record.medications.filter((m) => m.mrp && m.unit_rate && Number(m.unit_rate) > Number(m.mrp));
  const fieldProps = { claimId, canCorrect, onShow, onSaved: load };
  const scanned = record.documents.some((d) => d.quality && d.quality !== "good");

  return (
    <div className="record">
      {record.fields_needing_review > 0 && (
        <p className="notice-warn">
          {record.fields_needing_review} value{record.fields_needing_review === 1 ? " was" : "s were"} read with low
          confidence. They are marked in yellow below: check them against the page.
        </p>
      )}

      <div className="card">
        <dl className="facts">
          <div>
            <dt>Patient</dt>
            <dd>
              {s.patient_name ?? "—"}
              {s.age ? `, ${s.age}` : ""}
              {s.gender && s.gender !== "unknown" ? ` ${String(s.gender)[0].toUpperCase()}` : ""}
            </dd>
          </div>
          <div>
            <dt>Policy</dt>
            <dd>{s.policy_no ?? "—"}</dd>
          </div>
          <div>
            <dt>Hospital</dt>
            <dd>
              {s.hospital_name ?? "—"}
              {s.rohini_id && <div className="muted small">Rohini {s.rohini_id}</div>}
            </dd>
          </div>
          <div>
            <dt>Treating doctor</dt>
            <dd>
              {s.doctor_name ?? "—"}
              {s.doctor_reg_no && <div className="muted small">{s.doctor_reg_no}</div>}
            </dd>
          </div>
          <div>
            <dt>Stay</dt>
            <dd>
              {dateTime(s.admission)} → {dateTime(s.discharge)}
              {stayDays(s.admission, s.discharge) && <div className="muted small">{stayDays(s.admission, s.discharge)}</div>}
            </dd>
          </div>
          <div>
            <dt>Claimed</dt>
            <dd>
              <strong>{money(s.claimed_amount)}</strong>
              <div className="muted small">
                {record.bill_lines.length ? `bill lines add up to ${money(billTotal)}` : "bill lines not read"}
              </div>
            </dd>
          </div>
          <div>
            <dt>Sum insured</dt>
            <dd>{money(s.sum_insured)}</dd>
          </div>
          <div>
            <dt>Read by</dt>
            <dd className="small">
              {record.ai.calls === 0
                ? scanned
                  ? "Rules only: scanned pages need the AI reader (AI key not set)"
                  : "Rules only (digital text)"
                : `Rules + AI (${record.ai.calls} document${record.ai.calls === 1 ? "" : "s"}${
                    record.ai.failed ? `, ${record.ai.failed} failed` : ""
                  }) · ≈ $${Number(record.ai.cost_usd).toFixed(4)}`}
            </dd>
          </div>
        </dl>
      </div>

      <div className="record-grid">
        <div className="card">
          <h2>Diagnosis</h2>
          <ul className="plain-list">
            {record.diagnoses.map((d, i) => (
              <li key={i}>
                {d.description} {d.code && <code>{d.code}</code>}{" "}
                <span className="muted small">· {DOC_TYPE_LABELS[d.source_document ?? ""] ?? d.source_document}</span>
                <ShowLink pageId={d.page_id} bbox={d.bbox} onShow={onShow} />
              </li>
            ))}
            {record.diagnoses.length === 0 && <li className="muted">None found</li>}
          </ul>
        </div>
        <div className="card">
          <h2>Procedures</h2>
          <ul className="plain-list">
            {record.procedures.map((p, i) => (
              <li key={i}>
                {p.description}{" "}
                <span className="muted small">
                  · {p.source_document === "final_bill" ? "Bill" : DOC_TYPE_LABELS[p.source_document ?? ""] ?? p.source_document}
                  {p.performed_at ? `, ${dateTime(p.performed_at)}` : ""}
                </span>
                <ShowLink pageId={p.page_id} bbox={p.bbox} onShow={onShow} />
              </li>
            ))}
            {record.procedures.length === 0 && <li className="muted">Medical management (no procedure)</li>}
          </ul>
        </div>
      </div>

      <div className="card table-card">
        <div className="card-head pad">
          <h2>Bill ({record.bill_lines.length} lines)</h2>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Date</th>
              <th>Description</th>
              <th>Category</th>
              <th className="num">Qty</th>
              <th className="num">Rate</th>
              <th className="num">Amount</th>
              <th aria-label="Source" />
            </tr>
          </thead>
          <tbody>
            {record.bill_lines.map((b, i) => (
              <tr key={i}>
                <td className="muted">{b.line_no}</td>
                <td className="nowrap">{day(b.service_date)}</td>
                <td>{b.description}</td>
                <td className="muted small">{b.category.replace(/_/g, " ")}</td>
                <td className="num">{b.quantity !== null ? Number(b.quantity) : "—"}</td>
                <td className="num">{money(b.unit_rate)}</td>
                <td className="num">{money(b.amount)}</td>
                <td className="nowrap">
                  {b.method === "ai" && <span className="chip-small chip-ai">AI</span>}
                  <ShowLink pageId={b.page_id} bbox={b.bbox} onShow={onShow} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="record-grid">
        <div className="card table-card">
          <div className="card-head pad">
            <h2>Medicines ({record.medications.length})</h2>
            {(notCharted.length > 0 || aboveMrp.length > 0) && (
              <span className="small error-text">
                {notCharted.length > 0 && `${notCharted.length} not in the medication chart`}
                {notCharted.length > 0 && aboveMrp.length > 0 && " · "}
                {aboveMrp.length > 0 && `${aboveMrp.length} above MRP`}
              </span>
            )}
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Medicine</th>
                <th className="num">Qty</th>
                <th className="num">MRP</th>
                <th className="num">Charged</th>
                <th>Charted</th>
                <th aria-label="Source" />
              </tr>
            </thead>
            <tbody>
              {record.medications.map((m, i) => (
                <tr key={i}>
                  <td>{m.drug_name}</td>
                  <td className="num">{m.quantity !== null ? Number(m.quantity) : "—"}</td>
                  <td className="num">{money(m.mrp)}</td>
                  <td className={`num ${m.mrp && m.unit_rate && Number(m.unit_rate) > Number(m.mrp) ? "error-text" : ""}`}>
                    {money(m.unit_rate)}
                  </td>
                  <td>{m.in_medication_chart === null ? "—" : m.in_medication_chart ? "✓" : <span className="error-text">✗</span>}</td>
                  <td>
                    <ShowLink pageId={m.page_id} bbox={m.bbox} onShow={onShow} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card table-card">
          <div className="card-head pad">
            <h2>Tests and scans ({record.investigations.length})</h2>
            {unreported.length > 0 && <span className="small error-text">{unreported.length} billed without a report</span>}
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Test</th>
                <th>Date</th>
                <th>Billed</th>
                <th>Report</th>
                <th aria-label="Source" />
              </tr>
            </thead>
            <tbody>
              {record.investigations.map((t, i) => (
                <tr key={i}>
                  <td>
                    {t.test_name}
                    {t.kind && <span className="muted small"> · {t.kind}</span>}
                  </td>
                  <td className="nowrap">{day(t.performed_at)}</td>
                  <td>{t.billed ? "✓" : "—"}</td>
                  <td>{t.has_report ? "✓" : <span className="error-text">missing</span>}</td>
                  <td>
                    <ShowLink pageId={t.page_id} bbox={t.bbox} onShow={onShow} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <h2 className="section-title">Everything read, by document</h2>
      {record.documents.map((d) => (
        <DocumentCard key={d.key} doc={d} {...fieldProps} />
      ))}
    </div>
  );
}
