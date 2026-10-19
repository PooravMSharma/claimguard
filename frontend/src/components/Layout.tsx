import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { ROLE_LABELS } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const NAV: { to: string; label: string; permission: string }[] = [
  { to: "/claims", label: "Claim queue", permission: "claims.view" },
  { to: "/upload", label: "Upload claim", permission: "claims.upload" },
  { to: "/reference", label: "Reference data", permission: "reference.view" },
  { to: "/admin/users", label: "Users", permission: "users.manage" },
  { to: "/admin/audit", label: "Audit log", permission: "audit.view" },
  { to: "/status", label: "System status", permission: "system.view" },
];

export default function Layout() {
  const { user, logout, can } = useAuth();
  const navigate = useNavigate();

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">CG</span>
          <span className="brand-name">ClaimGuard</span>
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.filter((item) => can(item.permission)).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        {user && (
          <div className="user-box">
            <div className="user-name">{user.full_name}</div>
            <div className="user-role">{ROLE_LABELS[user.role]}</div>
            <button className="link-button" onClick={signOut}>
              Sign out
            </button>
          </div>
        )}
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
