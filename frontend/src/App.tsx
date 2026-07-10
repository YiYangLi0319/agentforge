import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import { useAuth } from "./stores/auth";

const AdminPage = lazy(() => import("./pages/AdminPage"));
const AgentsPage = lazy(() => import("./pages/AgentsPage"));
const ChatPage = lazy(() => import("./pages/ChatPage"));
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const DataPage = lazy(() => import("./pages/DataPage"));
const KnowledgePage = lazy(() => import("./pages/KnowledgePage"));
const LoginPage = lazy(() => import("./pages/LoginPage"));
const ResearchPage = lazy(() => import("./pages/ResearchPage"));
const SharedResearchPage = lazy(() => import("./pages/SharedResearchPage"));
const ToolsPage = lazy(() => import("./pages/ToolsPage"));
const TracesPage = lazy(() => import("./pages/TracesPage"));

function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuth((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/share/research/:token" element={<SharedResearchPage />} />
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
      </Suspense>
    </BrowserRouter>
  );
}

function PageFallback() {
  return (
    <div className="flex h-full items-center justify-center bg-zinc-950 text-sm text-zinc-500">
      <span className="mr-2 h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
      正在载入工作区…
    </div>
  );
}
