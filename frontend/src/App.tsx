import { Navigate, Route, Routes } from "react-router-dom";

import RequireAuth from "./auth/RequireAuth";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Placeholder from "./pages/Placeholder";
import ReferenceData from "./pages/reference/ReferenceData";
import SystemStatus from "./pages/SystemStatus";
import AuditLog from "./pages/admin/AuditLog";
import Users from "./pages/admin/Users";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/claims" replace />} />
        <Route
          path="/claims"
          element={
            <RequireAuth permission="claims.view">
              <Placeholder title="Claim queue" note="Built in Step 11." />
            </RequireAuth>
          }
        />
        <Route
          path="/upload"
          element={
            <RequireAuth permission="claims.upload">
              <Placeholder title="Upload claim" note="Built in Step 6." />
            </RequireAuth>
          }
        />
        <Route
          path="/reference"
          element={
            <RequireAuth permission="reference.view">
              <ReferenceData />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireAuth permission="users.manage">
              <Users />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/audit"
          element={
            <RequireAuth permission="audit.view">
              <AuditLog />
            </RequireAuth>
          }
        />
        <Route
          path="/status"
          element={
            <RequireAuth permission="system.view">
              <SystemStatus />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Placeholder title="Page not found" note="Check the address." />} />
      </Route>
    </Routes>
  );
}
