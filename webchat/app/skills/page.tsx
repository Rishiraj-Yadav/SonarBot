import { SkillsManager } from "../../components/SkillsManager";
import { WorkspaceHero } from "../../components/WorkspaceHero";

export default function SkillsPage() {
  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Skill control"
        title="Manage workflows that can be invoked by slash or by intent."
        description="Bundled and custom skills live here, with clear toggles and metadata so you can see what is available before asking SonarBot to use it."
        badges={[
          { label: "Triggers", value: "Slash + natural language" },
          { label: "Scope", value: "Bundled + custom" },
          { label: "Mode", value: "Per-skill toggles" },
        ]}
      />
      <SkillsManager />
    </main>
  );
}
