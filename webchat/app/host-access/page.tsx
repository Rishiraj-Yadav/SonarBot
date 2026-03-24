import { HostAccessPanel } from "../../components/HostAccessPanel";
import { WorkspaceHero } from "../../components/WorkspaceHero";

export default function HostAccessPage() {
  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Host access"
        title="Approve risky actions and review what touched the machine."
        description="Use this workspace to approve guarded host actions, inspect the audit trail, and restore file backups without mixing system-level control into everyday chat."
        badges={[
          { label: "Policy", value: "Path-based safeguards" },
          { label: "Writes", value: "Approval gated" },
          { label: "Backups", value: "Restore from audit" },
        ]}
      />
      <HostAccessPanel />
    </main>
  );
}
