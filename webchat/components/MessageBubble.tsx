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
      className={`max-w-[88%] rounded-[1.5rem] border px-4 py-3 shadow-card ${
        isUser
          ? "ml-auto border-accent/20 bg-accent text-white"
          : isAssistant
            ? "border-white/80 bg-white/95 text-ink"
            : "border-line bg-sand/70 text-ink"
      }`}
    >
      <div className="mb-1 text-[11px] uppercase tracking-[0.24em] opacity-70">{role}</div>
      <div className="whitespace-pre-wrap text-sm leading-6">{renderLinkedContent(content)}</div>
    </div>
  );
}
