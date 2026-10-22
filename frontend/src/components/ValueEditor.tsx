import { useState } from "react";

/**
 * Edits a settings value while keeping its shape: the shape comes from the default (or current) value.
 * number → number box · true/false → checkbox · text → text box · list of words → one per line ·
 * named numbers ({"syringe": 8}) → rows that can be added or removed · named settings → a group per name ·
 * anything else (lists of records) → JSON text. The server checks the shape again before saving.
 */

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

const label = (key: string) => key.replace(/_/g, " ");

const isNumberMap = (v: unknown): v is Record<string, number> =>
  !!v && typeof v === "object" && !Array.isArray(v) && Object.values(v).length > 0 &&
  Object.values(v).every((x) => typeof x === "number");

function NumberMap({ value, onChange }: { value: Record<string, number>; onChange: (v: Record<string, number>) => void }) {
  const [newKey, setNewKey] = useState("");
  return (
    <div className="value-map">
      {Object.entries(value).map(([k, v]) => (
        <div key={k} className="value-row">
          <span className="value-key">{k}</span>
          <input
            type="number"
            min={0}
            step="any"
            value={Number.isFinite(v) ? v : ""}
            onChange={(e) => onChange({ ...value, [k]: e.target.value === "" ? NaN : Number(e.target.value) })}
            aria-label={k}
          />
          <button
            type="button"
            className="link-button"
            onClick={() => {
              const next = { ...value };
              delete next[k];
              onChange(next);
            }}
          >
            Remove
          </button>
        </div>
      ))}
      <div className="value-row">
        <input value={newKey} placeholder="Add an item" onChange={(e) => setNewKey(e.target.value)} aria-label="New item" />
        <button
          type="button"
          className="link-button"
          disabled={!newKey.trim() || newKey.trim() in value}
          onClick={() => {
            onChange({ ...value, [newKey.trim()]: 0 });
            setNewKey("");
          }}
        >
          Add
        </button>
      </div>
    </div>
  );
}

function JsonField({ value, onChange, name }: { value: Json; onChange: (v: Json) => void; name: string }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [bad, setBad] = useState(false);
  return (
    <>
      <textarea
        className={`json-text ${bad ? "invalid" : ""}`}
        rows={Math.min(14, text.split("\n").length + 1)}
        value={text}
        aria-label={name}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setBad(false);
          } catch {
            setBad(true);
          }
        }}
      />
      {bad && <span className="error-text small">Not valid JSON yet.</span>}
    </>
  );
}

export function Field({ name, value, shape, onChange, readOnly }: {
  name: string;
  value: Json;
  shape: Json;
  onChange: (v: Json) => void;
  readOnly?: boolean;
}) {
  if (typeof shape === "boolean") {
    return (
      <input type="checkbox" checked={value === true} disabled={readOnly} onChange={(e) => onChange(e.target.checked)} aria-label={name} />
    );
  }
  if (typeof shape === "number") {
    return (
      <input
        type="number"
        min={0}
        step="any"
        className="num-input"
        value={typeof value === "number" && Number.isFinite(value) ? value : ""}
        readOnly={readOnly}
        onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
        aria-label={name}
      />
    );
  }
  if (typeof shape === "string") {
    return <input value={String(value ?? "")} readOnly={readOnly} onChange={(e) => onChange(e.target.value)} aria-label={name} />;
  }
  if (Array.isArray(shape) && shape.every((x) => typeof x === "string")) {
    const list = Array.isArray(value) ? (value as string[]) : [];
    return (
      <textarea
        rows={Math.min(10, Math.max(3, list.length + 1))}
        value={list.join("\n")}
        readOnly={readOnly}
        onChange={(e) => onChange(e.target.value.split("\n"))}
        aria-label={`${name} (one per line)`}
        placeholder="One per line"
      />
    );
  }
  if (isNumberMap(shape) && !readOnly) {
    return <NumberMap value={(value ?? {}) as Record<string, number>} onChange={(v) => onChange(v)} />;
  }
  if (shape && typeof shape === "object" && !Array.isArray(shape) && !readOnly && !isNumberMap(shape)) {
    const obj = (value ?? {}) as Record<string, Json>;
    return (
      <div className="value-group">
        {Object.entries(shape).map(([k, s]) => (
          <label key={k} className="value-field">
            <span>{label(k)}</span>
            <Field name={k} value={obj[k]} shape={s} onChange={(v) => onChange({ ...obj, [k]: v })} />
          </label>
        ))}
      </div>
    );
  }
  if (readOnly) return <code className="value-read">{JSON.stringify(value)}</code>;
  return <JsonField value={value} onChange={onChange} name={name} />;
}

/** Strip blank lines from word lists before saving. */
export function clean(value: Json): Json {
  if (Array.isArray(value)) {
    return value.every((x) => typeof x === "string")
      ? (value as string[]).map((x) => x.trim()).filter(Boolean)
      : value.map(clean);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, clean(v)]));
  }
  return value;
}

export type { Json };
