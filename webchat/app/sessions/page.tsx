import { SessionList } from "../../components/SessionList";

async function getHistory() {
  try {
    const response = await fetch("http://localhost:8765/webchat/history?session_key=main&limit=50", { cache: "no-store" });
    if (!response.ok) {
      return [];
    }
    const data = await response.json();
    return data.messages ?? [];
  } catch {
    return [];
  }
}

export default async function SessionsPage() {
  const history = await getHistory();
  return (
    <main className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.2em] text-accent">Archive</p>
        <h1 className="text-4xl font-semibold">Session Viewer</h1>
      </header>
      <SessionList messages={history} />
    </main>
  );
}
