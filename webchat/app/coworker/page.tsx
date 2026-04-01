import { CoworkerPanel } from "../../components/CoworkerPanel";
import { WorkspaceHero } from "../../components/WorkspaceHero";

export default function CoworkerPage() {
  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Coworker workspace"
        title="Watch the visual loop think, act, verify, and recover."
        description="This surface is dedicated to screenshot-driven desktop tasks. Review backend health, evidence captures, retry decisions, and the exact reason a task continued, stopped, or asked for approval."
        badges={[
          { label: "Targeting", value: "UIA + OCR boxes + Gemini" },
          { label: "Loop", value: "Capture -> decide -> act -> verify" },
          { label: "Safety", value: "Verified stop reasons" },
        ]}
      />
      <CoworkerPanel />
    </main>
  );
}
