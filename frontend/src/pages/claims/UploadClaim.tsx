import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import { STAGE_LABELS, type ClaimStage, type UploadResult } from "../../api/types";
import { fileSize } from "./common";

const MAX_BYTES = 100 * 1024 * 1024;
const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff";
const OK_TYPE = /\.(pdf|jpe?g|png|tiff?)$/i;

export default function UploadClaim() {
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [stage, setStage] = useState<ClaimStage>("final_cashless");
  const [claimNumber, setClaimNumber] = useState("");
  const [policyNumber, setPolicyNumber] = useState("");
  const [memberName, setMemberName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const total = files.reduce((sum, f) => sum + f.size, 0);
  const rejected = files.filter((f) => !OK_TYPE.test(f.name));

  const add = (list: FileList | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    setFiles((current) => {
      const names = new Set(current.map((f) => `${f.name}:${f.size}`));
      return [...current, ...incoming.filter((f) => !names.has(`${f.name}:${f.size}`))];
    });
    setError(null);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    add(e.dataTransfer.files);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!files.length) return setError("Add at least one file.");
    if (rejected.length) return setError(`Remove files that are not PDF, JPG, PNG or TIFF: ${rejected.map((f) => f.name).join(", ")}`);
    if (total > MAX_BYTES) return setError("The files are larger than 100 MB in total.");

    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    form.append("stage", stage);
    if (claimNumber.trim()) form.append("claim_number", claimNumber.trim());
    if (policyNumber.trim()) form.append("policy_number", policyNumber.trim());
    if (memberName.trim()) form.append("member_name", memberName.trim());

    setProgress(0);
    setError(null);
    try {
      const result = await api.upload<UploadResult>("/claims", form, setProgress);
      navigate(`/claims/${result.claim.id}`, { state: { warnings: result.warnings } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setProgress(null);
    }
  };

  const busy = progress !== null;

  return (
    <section>
      <div className="page-head">
        <h1 className="page-title">Upload claim</h1>
      </div>
      <form className="card upload-form" onSubmit={submit}>
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => input.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        >
          <strong>Drop the claim files here</strong>
          <span className="muted">or click to choose · PDF, JPG, PNG, TIFF · up to 150 pages and 100 MB per claim</span>
          <input
            ref={input}
            type="file"
            multiple
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              add(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {files.length > 0 && (
          <ul className="file-list">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className={OK_TYPE.test(f.name) ? "" : "file-bad"}>
                <span className="file-name">{f.name}</span>
                <span className="muted">{fileSize(f.size)}</span>
                {!busy && (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => setFiles((list) => list.filter((_, j) => j !== i))}
                  >
                    Remove
                  </button>
                )}
              </li>
            ))}
            <li className="file-total">
              <span>
                {files.length} file{files.length === 1 ? "" : "s"}
              </span>
              <span className={total > MAX_BYTES ? "error-text" : "muted"}>{fileSize(total)} of 100 MB</span>
            </li>
          </ul>
        )}

        <div className="form-grid">
          <label className="field">
            <span>Claim stage</span>
            <select value={stage} onChange={(e) => setStage(e.target.value as ClaimStage)} disabled={busy}>
              {(Object.keys(STAGE_LABELS) as ClaimStage[]).map((s) => (
                <option key={s} value={s}>
                  {STAGE_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Claim number</span>
            <input
              value={claimNumber}
              onChange={(e) => setClaimNumber(e.target.value)}
              placeholder="Optional: the TPA's own number"
              maxLength={64}
              disabled={busy}
            />
          </label>
          <label className="field">
            <span>Policy number</span>
            <input value={policyNumber} onChange={(e) => setPolicyNumber(e.target.value)} maxLength={64} disabled={busy} />
          </label>
          <label className="field">
            <span>Patient name</span>
            <input value={memberName} onChange={(e) => setMemberName(e.target.value)} maxLength={200} disabled={busy} />
          </label>
          <p className="muted small form-note">
            Policy number and patient name are optional: they are read from the documents later. Originals are stored
            unchanged.
          </p>
          <div className="form-actions">
            {error && (
              <p className="error-text" role="alert">
                {error}
              </p>
            )}
            {busy && (
              <div className="progress" aria-label="Upload progress">
                <div style={{ width: `${Math.round((progress ?? 0) * 100)}%` }} />
              </div>
            )}
            <button className="button" type="submit" disabled={busy || !files.length}>
              {busy ? (progress! < 1 ? `Uploading ${Math.round(progress! * 100)}%` : "Checking files…") : "Upload claim"}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}
