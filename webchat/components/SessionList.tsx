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
  const recentMessages = [...messages].slice(-12).reverse();

  return (
    <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel backdrop-blur">
      <div className="border-b border-line/70 pb-4">
        <p className="text-xs uppercase tracking-[0.24em] text-accent">Session Feed</p>
        <h2 className="mt-2 font-display text-3xl text-ink">Recent thread</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          Clean transcript preview from the active web session. Tool traces stay hidden so the feed reads like a real
          conversation.
        </p>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {recentMessages.length === 0 ? (
          <div className="rounded-[1.5rem] border border-dashed border-line/80 bg-foam/70 p-4 text-sm text-slate-500 md:col-span-2 2xl:col-span-3">
            No history yet. Send a message to start the current WebChat thread.
          </div>
        ) : null}
        {recentMessages.map((message) => (
          <div key={message.id} className="rounded-[1.35rem] border border-line/80 bg-white/90 p-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">{message.role}</div>
              <div
                className={`rounded-full px-2 py-1 text-[10px] uppercase tracking-[0.18em] ${
                  message.role === "user" ? "bg-glow text-accent" : "bg-sand text-slate-700"
                }`}
              >
                {message.role === "user" ? "Prompt" : "Reply"}
              </div>
            </div>
            <p className="line-clamp-4 text-sm leading-6 text-slate-700">{message.content}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
