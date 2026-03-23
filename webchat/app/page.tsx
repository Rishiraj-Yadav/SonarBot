import { AutomationPanel } from "../components/AutomationPanel";
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
      <header className="rounded-[2.25rem] border border-white/80 bg-white/85 p-7 shadow-panel backdrop-blur">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.28em] text-accent">Unified Control Plane</p>
            <h1 className="mt-2 max-w-3xl font-display text-5xl leading-[0.95] text-ink">Talk once, route everywhere.</h1>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-slate-600">
              WebChat shares the same gateway, memory, tools, OAuth flows, and automation engine as the CLI and Telegram
              channels.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="rounded-2xl bg-foam px-4 py-3 text-sm text-slate-600">
              <div className="text-xs uppercase tracking-[0.2em] text-accent">Realtime</div>
              WebSocket stream
            </div>
            <div className="rounded-2xl bg-sand px-4 py-3 text-sm text-slate-600">
              <div className="text-xs uppercase tracking-[0.2em] text-accent">Memory</div>
              Daily + long-term
            </div>
            <div className="rounded-2xl bg-glow px-4 py-3 text-sm text-slate-600">
              <div className="text-xs uppercase tracking-[0.2em] text-accent">Actions</div>
              OAuth, tools, agents
            </div>
          </div>
        </div>
      </header>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.55fr)_360px]">
        <ChatWindow />
        <div className="space-y-6">
          <SessionList messages={history} />
          <AutomationPanel />
        </div>
      </div>
    </main>
  );
}
