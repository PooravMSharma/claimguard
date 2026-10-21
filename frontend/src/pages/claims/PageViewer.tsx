import { useEffect, useMemo, useState } from "react";

import { api } from "../../api/client";
import {
  DOC_TYPE_LABELS,
  QUALITY_LABELS,
  type ClaimDetail,
  type Focus,
  type PageText,
  type QualityFlag,
} from "../../api/types";

interface FlatPage {
  id: string;
  filename: string;
  pageNumber: number;
  docType: string;
  confidence: number | null;
  source: string | null;
  quality: QualityFlag | null;
  ocrConfidence: number | null;
}

interface Props {
  claim: ClaimDetail;
  canEditType: boolean;
  onChanged: (claim: ClaimDetail) => void;
  focus?: Focus | null;
}

const TYPES = Object.keys(DOC_TYPE_LABELS);

function QualityDot({ flag }: { flag: QualityFlag | null }) {
  if (!flag) return null;
  return <span className={`qdot q-${flag}`} title={QUALITY_LABELS[flag]} />;
}

export default function PageViewer({ claim, canEditType, onChanged, focus }: Props) {
  const pages: FlatPage[] = useMemo(
    () =>
      claim.files.flatMap((f) =>
        f.pages.map((p) => ({
          id: p.id,
          filename: f.filename,
          pageNumber: p.page_number,
          docType: p.doc_type,
          confidence: p.doc_type_confidence,
          source: p.text_source,
          quality: p.quality_flag,
          ocrConfidence: p.ocr_confidence,
        })),
      ),
    [claim],
  );
  const [selected, setSelected] = useState(0);
  const [panel, setPanel] = useState<"none" | "text">("none");
  const [showWords, setShowWords] = useState(false);
  const [text, setText] = useState<PageText | null>(null);
  const [error, setError] = useState<string | null>(null);
  const current = pages[Math.min(selected, Math.max(pages.length - 1, 0))];

  // "Show on page" from the claim record: jump to the page and highlight the value
  useEffect(() => {
    if (!focus) return;
    const index = pages.findIndex((p) => p.id === focus.pageId);
    if (index >= 0) setSelected(index);
  }, [focus, pages]);
  const highlight = focus && current && focus.pageId === current.id ? focus.bbox : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (e.key === "ArrowRight") setSelected((s) => Math.min(s + 1, pages.length - 1));
      if (e.key === "ArrowLeft") setSelected((s) => Math.max(s - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pages.length]);

  const needText = panel === "text" || showWords;
  useEffect(() => {
    if (!current || !needText) return;
    let cancelled = false;
    setText(null);
    api
      .get<PageText>(`/claims/${claim.id}/pages/${current.id}/text`)
      .then((t) => !cancelled && setText(t))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : "Could not load text"));
    return () => {
      cancelled = true;
    };
  }, [claim.id, current, needText]);

  const changeType = async (docType: string) => {
    try {
      onChanged(await api.patch<ClaimDetail>(`/claims/${claim.id}/pages/${current.id}`, { doc_type: docType }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the page type");
    }
  };

  if (!pages.length || !current) return null;

  // Group consecutive pages of the same type for the thumbnail rail labels.
  return (
    <div className={`viewer ${panel === "text" ? "with-panel" : ""}`}>
      <div className="thumbs" aria-label="Pages">
        {pages.map((p, i) => {
          const startsGroup = i === 0 || pages[i - 1].docType !== p.docType || pages[i - 1].filename !== p.filename;
          return (
            <div key={p.id}>
              {startsGroup && <div className="thumb-group">{DOC_TYPE_LABELS[p.docType] ?? p.docType}</div>}
              <button
                className={i === selected ? "thumb active" : "thumb"}
                onClick={() => setSelected(i)}
                title={`${p.filename}, page ${p.pageNumber} · ${DOC_TYPE_LABELS[p.docType] ?? p.docType}`}
              >
                <img src={`/api/claims/${claim.id}/pages/${p.id}/image`} alt="" loading="lazy" />
                <span>
                  <QualityDot flag={p.quality} />
                  {i + 1}
                </span>
              </button>
            </div>
          );
        })}
      </div>

      <div className="page-view">
        <div className="page-bar">
          <button className="button button-quiet" disabled={selected === 0} onClick={() => setSelected(selected - 1)}>
            ‹
          </button>
          <div className="page-meta">
            {canEditType ? (
              <select
                className="type-select"
                value={current.docType}
                onChange={(e) => changeType(e.target.value)}
                aria-label="Page type"
              >
                {TYPES.map((t) => (
                  <option key={t} value={t}>
                    {DOC_TYPE_LABELS[t]}
                  </option>
                ))}
              </select>
            ) : (
              <strong>{DOC_TYPE_LABELS[current.docType] ?? current.docType}</strong>
            )}
            {current.confidence !== null && current.docType !== "unknown" && (
              <span className="muted">
                {current.confidence >= 1 ? "set by reviewer" : `${Math.round(current.confidence * 100)}% sure`}
              </span>
            )}
            {current.quality && (
              <span className={`quality-tag q-${current.quality}`}>
                {QUALITY_LABELS[current.quality]}
                {current.source === "ocr" && current.ocrConfidence !== null
                  ? ` · OCR ${Math.round(current.ocrConfidence * 100)}%`
                  : current.source === "text_layer"
                    ? " · digital text"
                    : ""}
              </span>
            )}
            <span className="muted">
              Page {selected + 1} of {pages.length} · {current.filename} p. {current.pageNumber}
            </span>
          </div>
          <div className="page-tools">
            <label className="check small">
              <input type="checkbox" checked={showWords} onChange={(e) => setShowWords(e.target.checked)} /> Words
            </label>
            <button
              className={`button button-quiet ${panel === "text" ? "pressed" : ""}`}
              onClick={() => setPanel(panel === "text" ? "none" : "text")}
            >
              Text
            </button>
            <button
              className="button button-quiet"
              disabled={selected >= pages.length - 1}
              onClick={() => setSelected(selected + 1)}
            >
              ›
            </button>
          </div>
        </div>
        {error && <p className="error-text small">{error}</p>}
        <div className="page-canvas">
          <img className="page-img" src={`/api/claims/${claim.id}/pages/${current.id}/image`} alt={`Page ${selected + 1}`} />
          {highlight && (
            <span
              key={focus?.stamp}
              className="focus-box"
              style={{
                left: `${(highlight.x0 - 0.006) * 100}%`,
                top: `${(highlight.y0 - 0.004) * 100}%`,
                width: `${(highlight.x1 - highlight.x0 + 0.012) * 100}%`,
                height: `${(highlight.y1 - highlight.y0 + 0.008) * 100}%`,
              }}
            />
          )}
          {showWords &&
            text?.id === current.id &&
            text.words.map(([word, conf, x0, y0, x1, y1], i) => (
              <span
                key={i}
                className={`word-box ${conf < 0.6 ? "low" : ""}`}
                style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
                title={`${word} · ${Math.round(conf * 100)}%`}
              />
            ))}
        </div>
      </div>

      {panel === "text" && (
        <aside className="text-panel">
          <div className="text-panel-head">
            <strong>Page text</strong>
            <span className="muted small">
              {text?.text_source === "ocr" ? "read by OCR" : text?.text_source === "text_layer" ? "from the PDF" : ""}
            </span>
          </div>
          {text === null ? (
            <p className="muted">Loading…</p>
          ) : text.text ? (
            <pre className="page-text">{text.text}</pre>
          ) : (
            <p className="muted">No text found on this page.</p>
          )}
        </aside>
      )}
    </div>
  );
}