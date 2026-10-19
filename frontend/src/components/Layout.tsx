import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/claims", label: "Claim queue" },
  { to: "/upload", label: "Upload claim" },
  { to: "/admin", label: "Admin" },
  { to: "/status", label: "System status" },
];

export default function Layout() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">CG</span>
          <span className="brand-name">ClaimGuard</span>
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">v0.1 · development</div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}