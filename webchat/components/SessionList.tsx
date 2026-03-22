"use client";

type HistoryMessage = {
  id: string;
  role: string;
  content: string;
};

type Props = {
  messages: HistoryMessage[];
};

export function SessionList({ messages }: Props) {
  return (
    <aside className="rounded-3xl border border-line bg-white p-4 shadow-card">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.18em] text-accent">Recent Session</h2>
      <div className="space-y-3">
        {messages.length === 0 ? <p className="text-sm text-slate-500">No history yet.</p> : null}
        {messages.map((message) => (
          <div key={message.id} className="rounded-2xl border border-line p-3">
            <div className="mb-1 text-xs uppercase tracking-[0.16em] text-slate-500">{message.role}</div>
            <p className="line-clamp-3 text-sm text-slate-700">{message.content}</p>
          </div>
        ))}
      </div>
    </aside>
  );
}
