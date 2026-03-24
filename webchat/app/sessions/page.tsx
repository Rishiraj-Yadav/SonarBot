import { SessionList } from "../../components/SessionList";
import { WorkspaceHero } from "../../components/WorkspaceHero";

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
      <WorkspaceHero
        eyebrow="Session archive"
        title="Review the current thread with room to read."
        description="This page stays focused on clean conversation history so you can scroll, inspect, and understand the active session without browser or automation panels competing for space."
        badges={[
          { label: "Source", value: "webchat_main history" },
          { label: "Format", value: "User + assistant only" },
          { label: "Purpose", value: "Review and context" },
        ]}
      />
      <SessionList messages={history} />
    </main>
  );
}
