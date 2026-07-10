import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import AdminPage from "./pages/AdminPage";
import AgentsPage from "./pages/AgentsPage";
import ChatPage from "./pages/ChatPage";
import DashboardPage from "./pages/DashboardPage";
import DataPage from "./pages/DataPage";
import KnowledgePage from "./pages/KnowledgePage";
import LoginPage from "./pages/LoginPage";
import ResearchPage from "./pages/ResearchPage";
import ToolsPage from "./pages/ToolsPage";
import TracesPage from "./pages/TracesPage";
import { useAuth } from "./stores/auth";

function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuth((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/traces" element={<TracesPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
