import { ChatWindow } from "../components/ChatWindow";
import { WorkspaceHero } from "../components/WorkspaceHero";

export default function Page() {
  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Unified control plane"
        title="Talk once. Switch workspaces when you need detail."
        description="The console stays focused on conversation, while browser automation, automation inboxes, host approvals, session history, and skill controls live in their own dedicated tabs on the left."
        badges={[
          { label: "Primary flow", value: "Conversation first" },
          { label: "Routing", value: "Tools + automations" },
          { label: "Channels", value: "Web, CLI, Telegram" },
        ]}
      />
      <ChatWindow />
    </main>
  );
}
