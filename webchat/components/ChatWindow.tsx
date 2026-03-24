"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { MessageBubble } from "./MessageBubble";
import { createGatewaySocket, fetchJson } from "../lib/gateway_client";

type ChatMessage = {
  id: string;
  role: string;
  content: string;
};

type HistoryResponse = {
  messages: ChatMessage[];
};

const deviceKey = "sonarbot-webchat-device-id";

function getDeviceId() {
  if (typeof window === "undefined") {
    return "webchat-local";
  }
  const existing = window.localStorage.getItem(deviceKey);
  if (existing) {
    return existing;
  }
  const created = crypto.randomUUID();
  window.localStorage.setItem(deviceKey, created);
  document.cookie = `sonarbot_webchat=${created}; path=/`;
  return created;
}

export function ChatWindow() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [socketReady, setSocketReady] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const activeRequestIdRef = useRef<string | null>(null);
  const queuedRequestIdsRef = useRef<string[]>([]);
  const requestReplyMapRef = useRef<Map<string, string>>(new Map());
  const ignoredInlineChunksRef = useRef<string[]>([]);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const deviceId = useMemo(() => getDeviceId(), []);
  const quickPrompts = [
    { label: "Check inbox", value: "check my inbox" },
    { label: "/skills", value: "/skills" },
    { label: "Open browser profile", value: "open github in the browser" },
    { label: "/cron list", value: "/cron list" },
  ];

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript) {
      return;
    }
    transcript.scrollTo({ top: transcript.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    fetchJson<HistoryResponse>("/webchat/history?session_key=main&limit=20")
      .then((data) => setMessages(data.messages))
      .catch(() => undefined);

    const connect = () => {
      const socket = createGatewaySocket(deviceId);
      socketRef.current = socket;
      socket.onopen = () => {
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        setSocketReady(true);
      };
      socket.onclose = () => {
        setSocketReady(false);
        socketRef.current = null;
        if (!disposed) {
          reconnectTimer = setTimeout(connect, 1500);
        }
      };
      socket.onmessage = (event) => {
        const frame = JSON.parse(event.data) as Record<string, unknown>;

        const replyIdForRequest = (requestId: string | null | undefined) => {
          if (!requestId) {
            return null;
          }
          return requestReplyMapRef.current.get(requestId) ?? null;
        };

        const cleanupRequest = (requestId: string | null | undefined) => {
          if (!requestId) {
            return;
          }
          requestReplyMapRef.current.delete(requestId);
          if (activeRequestIdRef.current === requestId) {
            activeRequestIdRef.current = queuedRequestIdsRef.current.shift() ?? null;
            return;
          }
          queuedRequestIdsRef.current = queuedRequestIdsRef.current.filter((item) => item !== requestId);
        };

        const currentReplyId = () => {
          const activeRequestId = activeRequestIdRef.current;
          if (!activeRequestId) {
            return null;
          }
          return requestReplyMapRef.current.get(activeRequestId) ?? null;
        };

        if (frame.type === "res") {
          const requestId = String(frame.id ?? "");
          const ok = Boolean(frame.ok);
          if (!ok) {
            const errorText = String(frame.error ?? "Unknown request error");
            const replyId = replyIdForRequest(requestId);
            setMessages((current) =>
              current.map((item) =>
                item.id === replyId ? { ...item, content: `[Error] ${errorText}` } : item,
              ),
            );
            cleanupRequest(requestId);
            return;
          }

          const payload = (frame.payload as Record<string, unknown> | undefined) ?? undefined;
          const queued = Boolean(payload?.queued);
          const commandResponse = typeof payload?.command_response === "string" ? payload.command_response : null;
          if (commandResponse) {
            ignoredInlineChunksRef.current.push(commandResponse);
            const replyId = replyIdForRequest(requestId);
            setMessages((current) => {
              const placeholder = [...current]
                .reverse()
                .find((item) => item.id === replyId && item.role === "assistant");
              if (placeholder) {
                return current.map((item) =>
                  item.id === placeholder.id ? { ...item, content: commandResponse } : item,
                );
              }
              const existing = current.find((item) => item.id === replyId);
              if (existing) {
                return current.map((item) =>
                  item.id === replyId ? { ...item, content: commandResponse } : item,
                );
              }
              return replyId ? [...current, { id: replyId, role: "assistant", content: commandResponse }] : current;
            });
          }
          if (!queued || commandResponse) {
            cleanupRequest(requestId);
          }
        }
        if (frame.type === "event" && frame.event === "agent.chunk") {
          const text = String((frame.payload as Record<string, unknown> | undefined)?.text ?? "");
          const ignoredIndex = ignoredInlineChunksRef.current.indexOf(text);
          if (ignoredIndex >= 0) {
            ignoredInlineChunksRef.current.splice(ignoredIndex, 1);
            return;
          }
          const replyId = currentReplyId();
          if (!replyId) {
            return;
          }
          setMessages((current) => {
            const existing = current.find((item) => item.id === replyId);
            if (!existing) {
              return [...current, { id: replyId, role: "assistant", content: text }];
            }
            if (existing.content === "Thinking...") {
              return current.map((item) =>
                item.id === replyId ? { ...item, content: text } : item,
              );
            }
            const existingContent = existing.content;
            return current.map((item) =>
              item.id === replyId ? { ...item, content: existingContent + text } : item,
            );
          });
        }
        if (frame.type === "event" && frame.event === "notification.created") {
          if (typeof window !== "undefined") {
            window.dispatchEvent(
              new CustomEvent("sonarbot:notification", {
                detail: (frame.payload as Record<string, unknown> | undefined) ?? {},
              }),
            );
          }
        }
        if (frame.type === "event" && typeof frame.event === "string" && frame.event.startsWith("browser.")) {
          if (typeof window !== "undefined") {
            window.dispatchEvent(
              new CustomEvent(`sonarbot:${frame.event}`, {
                detail: (frame.payload as Record<string, unknown> | undefined) ?? {},
              }),
            );
          }
        }
        if (frame.type === "event" && (frame.event === "host_approval.created" || frame.event === "host_approval.updated")) {
          if (typeof window !== "undefined") {
            window.dispatchEvent(
              new CustomEvent("sonarbot:host-approval", {
                detail: (frame.payload as Record<string, unknown> | undefined) ?? {},
              }),
            );
          }
        }
        if (frame.type === "event" && frame.event === "agent.done") {
          const requestId = activeRequestIdRef.current;
          const replyId = currentReplyId();
          setMessages((current) =>
            current.map((item) =>
              item.id === replyId && item.content.trim() === ""
                ? { ...item, content: "(no response)" }
                : item,
            ),
          );
          cleanupRequest(requestId);
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      socketRef.current?.close();
    };
  }, [deviceId]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!input.trim() || !socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      return;
    }
    const requestId = crypto.randomUUID();
    const replyId = crypto.randomUUID();
    requestReplyMapRef.current.set(requestId, replyId);
    if (activeRequestIdRef.current === null) {
      activeRequestIdRef.current = requestId;
    } else {
      queuedRequestIdsRef.current.push(requestId);
    }
    setMessages((current) => [
      ...current,
      { id: requestId, role: "user", content: input.trim() },
      { id: replyId, role: "assistant", content: "Thinking..." },
    ]);
    socketRef.current.send(
      JSON.stringify({
        type: "req",
        id: requestId,
        method: "agent.send",
        params: { message: input.trim() },
      }),
    );
    setInput("");
  }

  return (
    <section className="rounded-[2rem] border border-white/85 bg-white/88 p-5 shadow-panel backdrop-blur sm:p-6">
      <div className="flex flex-col gap-4 border-b border-line/70 pb-5 xl:flex-row xl:items-end xl:justify-between">
        <div className="max-w-3xl">
          <p className="text-xs uppercase tracking-[0.28em] text-accent">Active Console</p>
          <h2 className="mt-2 font-display text-3xl leading-none text-ink sm:text-4xl">One thread, every capability.</h2>
          <p className="mt-3 text-sm leading-7 text-slate-600">
            Keep the main conversation clean here, then jump to Browser, Automation, or Host Access when you want a
            dedicated operational view.
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <div
            className={`rounded-[1.2rem] px-4 py-3 text-sm ${
              socketReady ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
            }`}
          >
            <div className="text-[11px] uppercase tracking-[0.2em]">Realtime</div>
            <div className="mt-1">{socketReady ? "WebSocket ready" : "Reconnecting"}</div>
          </div>
          <div className="rounded-[1.2rem] bg-glow px-4 py-3 text-sm text-slate-700">
            <div className="text-[11px] uppercase tracking-[0.2em] text-accent">Session</div>
            <div className="mt-1">webchat_main</div>
          </div>
          <div className="rounded-[1.2rem] bg-sand px-4 py-3 text-sm text-slate-700">
            <div className="text-[11px] uppercase tracking-[0.2em] text-accent">Input mode</div>
            <div className="mt-1">Slash + natural</div>
          </div>
        </div>
      </div>

      <div
        ref={transcriptRef}
        className="mt-5 flex h-[64vh] flex-col gap-4 overflow-y-auto rounded-[1.75rem] border border-line/70 bg-gradient-to-b from-foam via-white to-white px-4 py-5 sm:px-5"
      >
        {messages.length === 0 ? (
          <div className="rounded-[1.5rem] border border-dashed border-line/80 bg-white/90 p-5 text-sm leading-7 text-slate-500">
            Start with a natural request like <span className="font-medium text-ink">check my inbox</span>, a browser
            task, or a slash command such as <span className="rounded bg-glow px-2 py-1 text-accent">/skills</span>.
          </div>
        ) : null}
        {messages.map((message) => (
          <MessageBubble key={message.id} role={message.role} content={message.content} />
        ))}
      </div>

      <div className="mt-5 rounded-[1.75rem] border border-line/70 bg-white/92 p-4">
        <form onSubmit={onSubmit} className="flex flex-col gap-3 lg:flex-row">
          <input
            className="min-w-0 flex-1 rounded-[1.25rem] border border-line/70 bg-foam/70 px-4 py-3 text-sm outline-none transition placeholder:text-slate-400 focus:border-accent"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask about Gmail, GitHub, memory, browser actions, or try /skills"
          />
          <button
            className="rounded-[1.25rem] bg-accent px-6 py-3 text-sm font-medium text-white transition hover:bg-ink"
            type="submit"
          >
            Send
          </button>
        </form>
        <div className="mt-4 flex flex-wrap gap-2">
          {quickPrompts.map((prompt) => (
            <button
              key={prompt.label}
              type="button"
              onClick={() => setInput(prompt.value)}
              className="rounded-full border border-line/70 bg-white px-3 py-2 text-xs text-slate-600 transition hover:border-accent hover:text-accent"
            >
              {prompt.label}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
