import { SkillsManager } from "../../components/SkillsManager";

export default function SkillsPage() {
  return (
    <main className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.2em] text-accent">Capabilities</p>
        <h1 className="text-4xl font-semibold">Skills</h1>
      </header>
      <SkillsManager />
    </main>
  );
}
