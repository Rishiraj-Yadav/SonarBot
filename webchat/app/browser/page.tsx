import { BrowserPanel } from "../../components/BrowserPanel";
import { WorkspaceHero } from "../../components/WorkspaceHero";

export default function BrowserPage() {
  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Browser workspace"
        title="Operate Playwright like its own desk."
        description="Inspect active tabs, watch live headed-browser snapshots, review logs, and track downloads without crowding the main chat surface."
        badges={[
          { label: "Profiles", value: "Named per site/account" },
          { label: "Downloads", value: "workspace/inbox/browser_downloads" },
          { label: "Streaming", value: "WebChat headed view" },
        ]}
      />
      <BrowserPanel />
    </main>
  );
}
