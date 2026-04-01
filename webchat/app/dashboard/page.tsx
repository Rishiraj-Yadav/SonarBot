import { WorkspaceHero } from "../../components/WorkspaceHero";
import { gatewayUrl } from "../../lib/backend";

async function getDashboard() {
  try {
    const response = await fetch(gatewayUrl("/api/dashboard"), { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    return response.json();
  } catch {
    return null;
  }
}

export default async function DashboardPage() {
  const dashboard = await getDashboard();
  const cards = [
    { label: "Token Count", value: dashboard?.session?.token_count ?? 0 },
    { label: "Recent Messages", value: dashboard?.recent_messages_count ?? 0 },
    { label: "Active Skills", value: dashboard?.active_skills_count ?? 0 },
    { label: "Uptime (s)", value: dashboard?.uptime_seconds ?? 0 },
  ];

  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Overview"
        title="High-level signals from the current SonarBot runtime."
        description="Use this page for a lightweight status read across tokens, activity, skills, and uptime without opening a more detailed workspace."
        badges={[
          { label: "Focus", value: "Runtime health" },
          { label: "Scope", value: "Session snapshot" },
          { label: "View", value: "Compact metrics" },
        ]}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <section key={card.label} className="rounded-[1.75rem] border border-white/85 bg-white/90 p-5 shadow-card">
            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{card.label}</p>
            <p className="mt-3 text-3xl font-semibold">{card.value}</p>
          </section>
        ))}
      </div>
    </main>
  );
}
