import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api } from "../../api/client";
import { ROLE_LABELS, type RoleName, type UserRow } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";

const ROLES = Object.keys(ROLE_LABELS) as RoleName[];

function formatDate(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export default function Users() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    try {
      setUsers(await api.get<UserRow[]>("/users"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load users");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const update = async (target: UserRow, change: Partial<Pick<UserRow, "role" | "is_active">>) => {
    setError(null);
    setNotice(null);
    try {
      const saved = await api.patch<UserRow>(`/users/${target.id}`, change);
      setUsers((rows) => rows.map((row) => (row.id === saved.id ? saved : row)));
      setNotice(`Updated ${saved.full_name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const resetPassword = async (target: UserRow) => {
    const password = window.prompt(`New password for ${target.full_name} (min 10 characters):`);
    if (!password) return;
    setError(null);
    setNotice(null);
    try {
      await api.post(`/users/${target.id}/password`, { password });
      setNotice(`Password reset for ${target.full_name}. Share it with them securely.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Password reset failed");
    }
  };

  return (
    <section>
      <div className="page-head">
        <h1 className="page-title">Users</h1>
        <button className="button" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Close" : "Add user"}
        </button>
      </div>

      {showForm && (
        <NewUserForm
          onCreated={(created) => {
            setUsers((rows) => [...rows, created]);
            setShowForm(false);
            setNotice(`Created ${created.full_name}`);
          }}
        />
      )}

      {error && <p className="error-text" role="alert">{error}</p>}
      {notice && <p className="notice-text">{notice}</p>}

      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Last sign-in</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {users.map((row) => {
              const isMe = row.id === me?.id;
              return (
                <tr key={row.id} className={row.is_active ? "" : "row-muted"}>
                  <td>
                    {row.full_name}
                    {isMe && <span className="tag">You</span>}
                  </td>
                  <td>{row.email}</td>
                  <td>
                    <select
                      value={row.role}
                      disabled={isMe}
                      aria-label={`Role for ${row.full_name}`}
                      onChange={(e) => update(row, { role: e.target.value as RoleName })}
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {ROLE_LABELS[role]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{row.is_active ? "Active" : "Deactivated"}</td>
                  <td>{formatDate(row.last_login_at)}</td>
                  <td className="row-actions">
                    <button className="link-button" onClick={() => resetPassword(row)}>
                      Reset password
                    </button>
                    {!isMe && (
                      <button className="link-button" onClick={() => update(row, { is_active: !row.is_active })}>
                        {row.is_active ? "Deactivate" : "Reactivate"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function NewUserForm({ onCreated }: { onCreated: (user: UserRow) => void }) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<RoleName>("adjudicator");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.post<UserRow>("/users", { full_name: fullName, email, role, password }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create user");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card form-grid" onSubmit={submit}>
      <label className="field">
        <span>Full name</span>
        <input value={fullName} onChange={(e) => setFullName(e.target.value)} required />
      </label>
      <label className="field">
        <span>Email</span>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label className="field">
        <span>Role</span>
        <select value={role} onChange={(e) => setRole(e.target.value as RoleName)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Temporary password</span>
        <input
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <small className="muted">Min 10 characters, letters plus numbers or symbols.</small>
      </label>
      <div className="form-actions">
        {error && <p className="error-text" role="alert">{error}</p>}
        <button className="button" type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create user"}
        </button>
      </div>
    </form>
  );
}