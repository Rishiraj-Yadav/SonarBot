import { AutomationPanel } from "../../components/AutomationPanel";
import { WorkspaceHero } from "../../components/WorkspaceHero";

export default function AutomationPage() {
  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Automation workspace"
        title="See background runs without polluting the live chat."
        description="Cron jobs, heartbeat-triggered standing orders, webhook notifications, and live rule control all stay together in a dedicated operations view."
        badges={[
          { label: "Inputs", value: "Cron + heartbeat + webhooks" },
          { label: "Delivery", value: "Primary channel first" },
          { label: "Policy", value: "Notify-first approvals" },
        ]}
      />
      <AutomationPanel />
    </main>
  );
}
