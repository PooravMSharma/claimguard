import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";

interface Props {
  children: ReactNode;
  permission?: string;
}

/** Sends signed-out users to the login page, and shows "no access" when a permission is missing. */
export default function RequireAuth({ children, permission }: Props) {
  const { user, loading, can } = useAuth();
  const location = useLocation();

  if (loading) return <div className="center-screen muted">Loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (permission && !can(permission)) {
    return (
      <section>
        <h1 className="page-title">No access</h1>
        <div className="card muted">Your role does not include this page. Ask an admin if you need it.</div>
      </section>
    );
  }
  return <>{children}</>;
}