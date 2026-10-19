import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api } from "../../api/client";
import type {
  DatasetSummary,
  IcdCode,
  NonPayable,
  NonPayableMatch,
  PackageRow,
  Paged,
  PolicyTermRow,
  PriceResult,
  TariffRow,
} from "../../api/types";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "non_payables", label: "Non-payable items" },
  { key: "tariffs", label: "CGHS rates" },
  { key: "packages", label: "PM-JAY packages" },
  { key: "icd10", label: "ICD-10" },
  { key: "policy_terms", label: "Policy terms" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

const LIST_LABELS: Record<number, string> = {
  1: "List I · not payable",
  2: "List II · part of room charges",
  3: "List III · part of procedure charges",
  4: "List IV · part of treatment cost",
};

const rupees = (value: string | number | null | undefined) =>
  value === null || value === undefined
    ? "—"
    : `₹${Number(value).toLocaleString("en-IN", {
        minimumFractionDigits: Number.isInteger(Number(value)) ? 0 : 2,
        maximumFractionDigits: 2,
      })}`;

const dateText = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "—";

const errorText = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

export default function ReferenceData() {
  const [tab, setTab] = useState<TabKey>(() => {
    try {
      const saved = localStorage.getItem("cg:reference-tab");
      return (TABS.find((t) => t.key === saved)?.key ?? "overview") as TabKey;
    } catch {
      return "overview";
    }
  });

  const choose = (key: TabKey) => {
    setTab(key);
    try {
      localStorage.setItem("cg:reference-tab", key);
    } catch {
      // storage unavailable: tab just isn't remembered
    }
  };

  return (
    <section>
      <div className="page-head">
        <h1 className="page-title">Reference data</h1>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={tab === t.key ? "tab active" : "tab"}
            onClick={() => choose(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview />}
      {tab === "non_payables" && <NonPayables />}
      {tab === "tariffs" && <Tariffs />}
      {tab === "packages" && <Packages />}
      {tab === "icd10" && <Icd10 />}
      {tab === "policy_terms" && <PolicyTerms />}
    </section>
  );
}

// ---------------------------------------------------------------- overview

function Overview() {
  const [data, setData] = useState<DatasetSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<DatasetSummary[]>("/reference/summary")
      .then(setData)
      .catch((err) => setError(errorText(err, "Could not load summary")));
  }, []);

  if (error) return <p className="error-text" role="alert">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Rows</th>
              <th>Version</th>
              <th>Source</th>
              <th>In force from</th>
              <th>Loaded</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => {
              const imp = d.latest_import;
              return (
                <tr key={d.dataset} className={d.rows === 0 ? "row-muted" : ""}>
                  <td>{d.label}</td>
                  <td>{d.rows.toLocaleString("en-IN")}</td>
                  <td>{imp ? <span className="tag">{imp.version}</span> : <span className="tag tag-bad">Not loaded</span>}</td>
                  <td className="source-cell">
                    {imp ? (
                      imp.source_url ? (
                        <a href={imp.source_url} target="_blank" rel="noreferrer">
                          {imp.source_name}
                        </a>
                      ) : (
                        imp.source_name
                      )
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="nowrap">{dateText(imp?.effective_from)}</td>
                  <td className="nowrap">{dateText(imp?.imported_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted">
        Reference data is loaded by an admin with <code>python -m app.cli</code>. Every row keeps a link to the
        official document it came from.
      </p>
    </>
  );
}

// ---------------------------------------------------------------- non-payables

function NonPayables() {
  const [listNumber, setListNumber] = useState<number | "">("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<NonPayable[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams();
    if (listNumber) params.set("list_number", String(listNumber));
    if (q.trim()) params.set("q", q.trim());
    const timer = setTimeout(() => {
      api
        .get<NonPayable[]>(`/reference/non-payables?${params.toString()}`)
        .then((rows) => {
          setItems(rows);
          setError(null);
        })
        .catch((err) => setError(errorText(err, "Could not load items")));
    }, 200);
    return () => clearTimeout(timer);
  }, [listNumber, q]);

  return (
    <>
      <MatchTester />
      <div className="toolbar">
        <input placeholder="Search items" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search items" />
        <select
          value={listNumber}
          onChange={(e) => setListNumber(e.target.value ? Number(e.target.value) : "")}
          aria-label="Filter by list"
        >
          <option value="">All lists</option>
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={n}>
              {LIST_LABELS[n]}
            </option>
          ))}
        </select>
        <span className="muted">{items.length} items</span>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>List</th>
              <th>Item</th>
              <th>Also matches</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td className="nowrap">
                  <span className={`tag list-${item.list_number}`}>List {["I", "II", "III", "IV"][item.list_number - 1]}</span>
                </td>
                <td>{item.item_name}</td>
                <td className="muted">{item.synonyms.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function MatchTester() {
  const [text, setText] = useState("Surgical gloves\nInj. Ceftriaxone 1g\nAdmission kit\nUrinebag 2000ml");
  const [results, setResults] = useState<NonPayableMatch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (event: FormEvent) => {
    event.preventDefault();
    const lines = text
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .slice(0, 500);
    if (!lines.length) return;
    setBusy(true);
    try {
      setResults(await api.post<NonPayableMatch[]>("/reference/non-payables/match", { lines }));
      setError(null);
    } catch (err) {
      setError(errorText(err, "Matching failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={run}>
      <div className="card-head">
        <h2>Check bill lines</h2>
        <button className="button" type="submit" disabled={busy}>
          {busy ? "Checking…" : "Check"}
        </button>
      </div>
      <textarea
        className="textarea"
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        aria-label="Bill lines, one per line"
      />
      <small className="muted">One bill line per row. Each is checked against the IRDAI lists.</small>
      {error && <p className="error-text" role="alert">{error}</p>}
      {results && (
        <ul className="match-list">
          {results.map((r, i) => (
            <li key={i}>
              <span className="match-line">{r.line}</span>
              {r.matched ? (
                <span>
                  <span className={`tag list-${r.list_number}`}>{LIST_LABELS[r.list_number ?? 1]}</span> {r.item_name}
                  {r.method === "fuzzy" && <span className="muted"> (close match, {Math.round((r.score ?? 0) * 100)}%)</span>}
                </span>
              ) : (
                <span className="muted">Not on the lists</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}

// ---------------------------------------------------------------- tariffs

function usePaged<T>(path: string, params: Record<string, string>, pageSize = 50) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Paged<T> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = JSON.stringify(params);

  useEffect(() => setOffset(0), [key]);

  const load = useCallback(async () => {
    const query = new URLSearchParams({ ...JSON.parse(key), limit: String(pageSize), offset: String(offset) });
    try {
      setPage(await api.get<Paged<T>>(`${path}?${query.toString()}`));
      setError(null);
    } catch (err) {
      setError(errorText(err, "Could not load"));
    }
  }, [path, key, offset, pageSize]);

  useEffect(() => {
    const timer = setTimeout(load, 200);
    return () => clearTimeout(timer);
  }, [load]);

  return { page, error, offset, setOffset, pageSize };
}

function Pager({ total, offset, pageSize, setOffset }: { total: number; offset: number; pageSize: number; setOffset: (n: number) => void }) {
  if (total <= pageSize) return null;
  return (
    <div className="pager">
      <button className="button button-quiet" disabled={offset === 0} onClick={() => setOffset(offset - pageSize)}>
        Previous
      </button>
      <span className="muted">
        {offset + 1}–{Math.min(offset + pageSize, total)} of {total.toLocaleString("en-IN")}
      </span>
      <button className="button button-quiet" disabled={offset + pageSize >= total} onClick={() => setOffset(offset + pageSize)}>
        Next
      </button>
    </div>
  );
}

const gradeLabel = (row: TariffRow) =>
  row.super_speciality ? "Super speciality" : row.nabh_accredited === false ? "Non-NABH" : "NABH";

function Tariffs() {
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const params: Record<string, string> = { source: "cghs" };
  if (q.trim()) params.q = q.trim();
  const { page, error, offset, setOffset, pageSize } = usePaged<TariffRow>("/reference/tariffs", params);

  return (
    <>
      <PriceCalculator code={selected} />
      <div className="toolbar">
        <input placeholder="Search code or procedure" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search rates" />
        <span className="muted">Rates in force today. Click a row to price it.</span>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Procedure / investigation</th>
              <th>Grade</th>
              <th>Base rate</th>
              <th>Ward</th>
            </tr>
          </thead>
          <tbody>
            {page?.items.map((row) => (
              <tr key={row.id} className={`clickable ${selected === row.code ? "row-selected" : ""}`} onClick={() => setSelected(row.code)}>
                <td className="nowrap">
                  <code>{row.code}</code>
                </td>
                <td>
                  {row.name}
                  {row.category && <div className="muted small">{row.category}</div>}
                </td>
                <td className="nowrap">{gradeLabel(row)}</td>
                <td className="nowrap">{rupees(row.rate)}</td>
                <td className="nowrap muted">{row.ward_uniform ? "Same for all" : "Varies"}</td>
              </tr>
            ))}
            {page && page.items.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  No CGHS rates loaded yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {page && <Pager total={page.total} offset={offset} pageSize={pageSize} setOffset={setOffset} />}
    </>
  );
}

function PriceCalculator({ code }: { code: string | null }) {
  const [form, setForm] = useState({ code: "", city_tier: "tier_1", grade: "nabh", ward_type: "semi_private", on: "" });
  const [result, setResult] = useState<PriceResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (code) setForm((f) => ({ ...f, code }));
  }, [code]);

  const calculate = useCallback(async () => {
    if (!form.code.trim()) return;
    const params = new URLSearchParams({ city_tier: form.city_tier, grade: form.grade, ward_type: form.ward_type });
    if (form.on) params.set("on", form.on);
    try {
      setResult(await api.get<PriceResult>(`/reference/tariffs/cghs/${encodeURIComponent(form.code.trim())}/price?${params}`));
      setError(null);
    } catch (err) {
      setResult(null);
      setError(errorText(err, "Could not price"));
    }
  }, [form]);

  useEffect(() => {
    if (code) calculate();
  }, [code, calculate]);

  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }));

  return (
    <form
      className="card"
      onSubmit={(e) => {
        e.preventDefault();
        calculate();
      }}
    >
      <div className="card-head">
        <h2>CGHS rate calculator</h2>
      </div>
      <div className="calc-grid">
        <label className="field">
          <span>CGHS code</span>
          <input value={form.code} onChange={set("code")} placeholder="e.g. CN001" />
        </label>
        <label className="field">
          <span>City tier</span>
          <select value={form.city_tier} onChange={set("city_tier")}>
            <option value="tier_1">Tier I (X)</option>
            <option value="tier_2">Tier II (Y)</option>
            <option value="tier_3">Tier III (Z)</option>
          </select>
        </label>
        <label className="field">
          <span>Hospital</span>
          <select value={form.grade} onChange={set("grade")}>
            <option value="nabh">NABH</option>
            <option value="non_nabh">Non-NABH</option>
            <option value="super_speciality">Super speciality</option>
          </select>
        </label>
        <label className="field">
          <span>Ward</span>
          <select value={form.ward_type} onChange={set("ward_type")}>
            <option value="general">General</option>
            <option value="semi_private">Semi-private</option>
            <option value="private">Private</option>
          </select>
        </label>
        <label className="field">
          <span>Admission date</span>
          <input type="date" value={form.on} onChange={set("on")} />
        </label>
        <div className="calc-action">
          <button className="button" type="submit">
            Calculate
          </button>
        </div>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      {result && (
        <div className="calc-result">
          <div className="calc-total">
            <span className="muted">
              {result.code} · {result.name}
            </span>
            <strong>{rupees(result.rate)}</strong>
          </div>
          <ol className="calc-steps">
            <li>
              Published rate ({result.base_grade.replace("_", "-")}, {result.base_city_tier.replace("_", " ")}, semi-private):{" "}
              {rupees(result.base_rate)}
            </li>
            {result.adjustments.map((a, i) => (
              <li key={i} title={a.note}>
                {a.step}: {a.factor}
              </li>
            ))}
          </ol>
          <small className="muted">
            Rates in force from {dateText(result.effective_from)} · {result.source_name ?? "source not recorded"}
          </small>
        </div>
      )}
    </form>
  );
}

// ---------------------------------------------------------------- packages

function Packages() {
  const [q, setQ] = useState("");
  const params: Record<string, string> = {};
  if (q.trim()) params.q = q.trim();
  const { page, error, offset, setOffset, pageSize } = usePaged<PackageRow>("/reference/packages", params);

  return (
    <>
      <div className="toolbar">
        <input placeholder="Search code or package" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search packages" />
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Package</th>
              <th>Specialty</th>
              <th>Rate</th>
              <th>Stay</th>
              <th>Version</th>
            </tr>
          </thead>
          <tbody>
            {page?.items.map((p) => (
              <tr key={p.id}>
                <td className="nowrap">
                  <code>{p.code}</code>
                </td>
                <td>
                  {p.name}
                  {p.mandatory_documents.length > 0 && (
                    <div className="muted small">Documents: {p.mandatory_documents.join(", ")}</div>
                  )}
                </td>
                <td>{p.specialty ?? "—"}</td>
                <td className="nowrap">{rupees(p.rate)}</td>
                <td className="nowrap">{p.is_day_care ? "Day care" : p.expected_los_days ? `${p.expected_los_days} days` : "—"}</td>
                <td>
                  <span className="tag">{p.version}</span>
                </td>
              </tr>
            ))}
            {page && page.items.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No PM-JAY packages loaded yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {page && <Pager total={page.total} offset={offset} pageSize={pageSize} setOffset={setOffset} />}
    </>
  );
}

// ---------------------------------------------------------------- ICD-10

function Icd10() {
  const [q, setQ] = useState("");
  const [billable, setBillable] = useState(false);
  const params: Record<string, string> = { q: q.trim() };
  if (billable) params.billable_only = "true";
  const enabled = q.trim().length >= 2;

  return (
    <>
      <div className="toolbar">
        <input placeholder="Code (K35.8) or words (appendicitis)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search ICD-10" />
        <label className="check">
          <input type="checkbox" checked={billable} onChange={(e) => setBillable(e.target.checked)} /> Billable codes only
        </label>
      </div>
      {enabled ? <IcdResults params={params} /> : <p className="muted">Type at least 2 characters.</p>}
      <p className="muted small">
        Code set: ICD-10-CM (public domain). Hospitals in India code in WHO ICD-10; codes that differ are matched at
        the 3-character category level.
      </p>
    </>
  );
}

function IcdResults({ params }: { params: Record<string, string> }) {
  const { page, error, offset, setOffset, pageSize } = usePaged<IcdCode>("/reference/icd10", params);
  return (
    <>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Description</th>
              <th>Chapter</th>
            </tr>
          </thead>
          <tbody>
            {page?.items.map((c) => (
              <tr key={c.code}>
                <td className="nowrap">
                  <code>{c.code}</code>
                  {!c.is_billable && <span className="tag">not billable</span>}
                </td>
                <td>{c.description}</td>
                <td className="muted small">{c.chapter ?? "—"}</td>
              </tr>
            ))}
            {page && page.items.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  No codes found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {page && <Pager total={page.total} offset={offset} pageSize={pageSize} setOffset={setOffset} />}
    </>
  );
}

// ---------------------------------------------------------------- policy terms

function formatValue(value: Record<string, unknown>): string {
  if (value.codes && typeof value.codes === "object") return `${Object.keys(value.codes as object).length} exclusion codes`;
  return Object.entries(value)
    .filter(([k]) => k !== "typical")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`)
    .join(" · ");
}

function PolicyTerms() {
  const [terms, setTerms] = useState<PolicyTermRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<PolicyTermRow[]>("/reference/policy-terms")
      .then(setTerms)
      .catch((err) => setError(errorText(err, "Could not load policy terms")));
  }, []);

  if (error) return <p className="error-text" role="alert">{error}</p>;
  if (!terms) return <p className="muted">Loading…</p>;

  return (
    <>
      <p className="muted">
        Standard IRDAI terms, used until a client supplies its own policy wording. Terms marked <span className="tag">typical</span>{" "}
        are common market values, not regulatory limits.
      </p>
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Term</th>
              <th>Value</th>
              <th>Meaning</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {terms.map((t) => (
              <tr key={t.key}>
                <td className="nowrap">
                  <code>{t.key}</code>
                  {Boolean(t.value.typical) && <span className="tag">typical</span>}
                </td>
                <td>{formatValue(t.value)}</td>
                <td className="small">{t.description}</td>
                <td className="muted small">{t.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
