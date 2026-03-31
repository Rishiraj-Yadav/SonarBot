import { fetchJson } from "../../lib/gateway_client";
import { WorkspaceHero } from "../../components/WorkspaceHero";

async function getSettings() {
  try {
    return await fetchJson("/api/settings");
  } catch {
    return null;
  }
}

export default async function SettingsPage() {
  const settings = await getSettings();
  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Runtime settings"
        title="Inspect the backend snapshot that drives this workspace."
        description="This is a read-mostly operational view for checking what the gateway is exposing right now without leaving the app shell."
        badges={[
          { label: "Origin", value: "Gateway settings API" },
          { label: "Use", value: "Runtime verification" },
          { label: "Surface", value: "Read-only snapshot" },
        ]}
      />
      <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel">
        <pre className="overflow-x-auto whitespace-pre-wrap text-sm leading-6 text-slate-700">
          {JSON.stringify(settings ?? { status: "Gateway unavailable" }, null, 2)}
        </pre>
      </section>
    </main>
  );
}
