"use client";

import { Fragment, ReactNode } from "react";

type Props = {
  role: string;
  content: string;
};

const urlPattern = /(https?:\/\/[^\s]+)/g;

function renderLinkedContent(content: string): ReactNode {
  const lines = content.split("\n");
  return lines.map((line, lineIndex) => {
    const parts = line.split(urlPattern);
    return (
      <Fragment key={`line-${lineIndex}`}>
        {parts.map((part, partIndex) => {
          if (urlPattern.test(part)) {
            urlPattern.lastIndex = 0;
            return (
              <a
                key={`part-${lineIndex}-${partIndex}`}
                href={part}
                target="_blank"
                rel="noreferrer"
                className="break-all text-sky-600 underline underline-offset-2"
              >
                {part}
              </a>
            );
          }
          urlPattern.lastIndex = 0;
          return <Fragment key={`part-${lineIndex}-${partIndex}`}>{part}</Fragment>;
        })}
        {lineIndex < lines.length - 1 ? <br /> : null}
      </Fragment>
    );
  });
}

export function MessageBubble({ role, content }: Props) {
  const isUser = role === "user";
  const isAssistant = role === "assistant";
  return (
    <div
      className={`max-w-[90%] rounded-[1.65rem] border px-4 py-4 shadow-card ${
        isUser
          ? "ml-auto border-accent/15 bg-gradient-to-br from-accent to-[#17499f] text-white"
          : isAssistant
            ? "border-white/85 bg-white/96 text-ink"
            : "border-line bg-sand/80 text-ink"
      }`}
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-[11px] uppercase tracking-[0.24em] opacity-70">{role}</div>
        <div
          className={`rounded-full px-2 py-1 text-[10px] uppercase tracking-[0.18em] ${
            isUser
              ? "bg-white/15 text-white"
              : isAssistant
                ? "bg-glow text-accent"
                : "bg-white/60 text-slate-600"
          }`}
        >
          {isUser ? "Prompt" : isAssistant ? "Reply" : "Event"}
        </div>
      </div>
      <div className="whitespace-pre-wrap text-sm leading-7">{renderLinkedContent(content)}</div>
    </div>
  );
}
