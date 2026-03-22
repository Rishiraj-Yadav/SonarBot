import { ChatWindow } from "../components/ChatWindow";
import { SessionList } from "../components/SessionList";

async function getHistory() {
  try {
    const response = await fetch("http://localhost:8765/webchat/history?session_key=main&limit=10", { cache: "no-store" });
    if (!response.ok) {
      return [];
    }
    const data = await response.json();
    return data.messages ?? [];
  } catch {
    return [];
  }
}

export default async function Page() {
  const history = await getHistory();
  return (
    <main className="space-y-6">
      <header className="rounded-[2rem] border border-line bg-white/90 p-6 shadow-card">
        <p className="text-xs uppercase tracking-[0.2em] text-accent">Phase 3</p>
        <h1 className="mt-2 text-4xl font-semibold">WebChat Control Plane</h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
          Real-time chat, session history, skill awareness, and automation controls backed by the same gateway that powers
          the CLI and Telegram channel.
        </p>
      </header>
      <div className="grid gap-6 xl:grid-cols-[1.4fr_0.8fr]">
        <ChatWindow />
        <SessionList messages={history} />
      </div>
    </main>
  );
}
