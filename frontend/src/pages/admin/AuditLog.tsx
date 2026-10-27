import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { AuditEntry, AuditPage } from "../../api/types";

const PAGE_SIZE = 50;

const ACTION_FILTERS = [
  { value: "", label: "All actions" },
  { value: "login", label: "Sign-ins" },
  { value: "logout", label: "Sign-outs" },
  { value: "user.", label: "User changes" },
];

function describe(entry: AuditEntry): string {
  const d = entry.details ?? {};
  switch (entry.action) {
    case "login.success":
      return "Signed in";
    case "login.failed":
      return `Failed sign-in for ${String(d.email ?? "unknown")}${d.reason === "inactive" ? " (account deactivated)" : ""}`;
    case "login.locked":
      return `Sign-in blocked (locked) for ${String(d.email ?? "unknown")}`;
    case "logout":
      return "Signed out";
    case "user.created":
      return `Created user ${String(d.email ?? "")} as ${String(d.role ?? "")}`;
    case "user.password_reset":
      return "Reset a user's password";
    case "user.updated":
      return `Changed ${Object.keys(d).join(", ")}`;
    default:
      return entry.action;
  }
}

export default function AuditLog() {
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (action) params.set("action", action);
    try {
      setPage(await api.get<AuditPage>(`/audit?${params.toString()}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load audit log");
    }
  }, [action, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const total = page?.total ?? 0;

  return (
    <section>
      <div className="page-head">
        <h1 className="page-title">Audit log</h1>
        <select
          value={action}
          aria-label="Filter by action"
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
        >
          {ACTION_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="error-text" role="alert">{error}</p>}

      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>What</th>
              <th>IP address</th>
            </tr>
          </thead>
          <tbody>
            {page?.items.map((entry) => (
              <tr key={entry.id}>
                <td className="nowrap">
                  {new Date(entry.at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "medium" })}
                </td>
                <td>{entry.user_email ?? <span className="muted">—</span>}</td>
                <td>
                  <span className={`tag ${entry.action.endsWith("failed") || entry.action.endsWith("locked") ? "tag-bad" : ""}`}>
                    {entry.action}
                  </span>{" "}
                  {describe(entry)}
                </td>
                <td className="muted">{entry.ip_address ?? "—"}</td>
              </tr>
            ))}
            {page && page.items.length === 0 && (
              <tr>
                <td colSpan={4} className="muted">
                  No entries.
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
