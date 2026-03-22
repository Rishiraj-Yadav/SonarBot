"use client";

type Props = {
  role: string;
  content: string;
};

export function MessageBubble({ role, content }: Props) {
  const isUser = role === "user";
  return (
    <div className={`rounded-2xl px-4 py-3 shadow-card ${isUser ? "bg-accent text-white" : "bg-white text-ink"}`}>
      <div className="mb-1 text-xs uppercase tracking-[0.2em] opacity-70">{role}</div>
      <div className="whitespace-pre-wrap text-sm leading-6">{content}</div>
    </div>
  );
}
