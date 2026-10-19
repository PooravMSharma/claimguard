import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import Placeholder from "./pages/Placeholder";
import SystemStatus from "./pages/SystemStatus";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/status" replace />} />
        <Route
          path="/claims"
          element={<Placeholder title="Claim queue" note="Built in Step 11." />}
        />
        <Route
          path="/upload"
          element={<Placeholder title="Upload claim" note="Built in Step 6." />}
        />
        <Route
          path="/admin"
          element={<Placeholder title="Admin" note="Built in Steps 4 and 12." />}
        />
        <Route path="/status" element={<SystemStatus />} />
        <Route path="*" element={<Placeholder title="Page not found" note="Check the address." />} />
      </Route>
    </Routes>
  );
}