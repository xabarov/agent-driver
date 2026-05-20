import { ChatPage } from "./components/chat/ChatPage";
import { AppShell } from "./components/layout/AppShell";
import { Header } from "./components/layout/Header";

export default function App() {
  return <AppShell header={<Header />}><ChatPage /></AppShell>;
}
