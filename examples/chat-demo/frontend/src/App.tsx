import { Navigate, Route, Routes } from "react-router-dom";

import { ChatPage } from "./components/chat/ChatPage";
import { AppShell } from "./components/layout/AppShell";
import { Header } from "./components/layout/Header";
import { Sidebar } from "./components/sessions/Sidebar";
import { ReplayPage } from "./pages/ReplayPage";

export default function App() {
  return (
    <AppShell header={<Header />} sidebar={<Sidebar />}>
      <Routes>
        <Route path="/" element={<Navigate to="/sessions/new" replace />} />
        <Route path="/sessions/new" element={<ChatPage mode="new" />} />
        <Route path="/sessions/:id" element={<ChatPage mode="existing" />} />
        <Route path="/sessions/:id/replay/:runId" element={<ReplayPage />} />
      </Routes>
    </AppShell>
  );
}
