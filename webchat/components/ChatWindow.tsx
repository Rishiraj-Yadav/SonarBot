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
  const pendingIdRef = useRef<string | null>(null);
  const currentReplyId = useRef<string | null>(null);
  const ignoredInlineChunkRef = useRef<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const deviceId = useMemo(() => getDeviceId(), []);

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
        if (frame.type === "res") {
          const ok = Boolean(frame.ok);
          if (!ok) {
            const errorText = String(frame.error ?? "Unknown request error");
            setMessages((current) =>
              current.map((item) =>
                item.id === currentReplyId.current ? { ...item, content: `[Error] ${errorText}` } : item,
              ),
            );
            currentReplyId.current = null;
            ignoredInlineChunkRef.current = null;
            pendingIdRef.current = null;
            return;
          }

          const payload = (frame.payload as Record<string, unknown> | undefined) ?? undefined;
          const commandResponse = typeof payload?.command_response === "string" ? payload.command_response : null;
          if (commandResponse) {
            ignoredInlineChunkRef.current = commandResponse;
            setMessages((current) => {
              const replyId = currentReplyId.current ?? crypto.randomUUID();
              currentReplyId.current = replyId;
              const placeholder = [...current]
                .reverse()
                .find((item) => item.role === "assistant" && item.content === "Thinking...");
              if (placeholder) {
                return current.map((item) =>
                  item.id === placeholder.id ? { ...item, id: replyId, content: commandResponse } : item,
                );
              }
              const existing = current.find((item) => item.id === replyId);
              if (existing) {
                return current.map((item) =>
                  item.id === replyId ? { ...item, content: commandResponse } : item,
                );
              }
              return [...current, { id: replyId, role: "assistant", content: commandResponse }];
            });
          }
        }
        if (frame.type === "event" && frame.event === "agent.chunk") {
          const text = String((frame.payload as Record<string, unknown> | undefined)?.text ?? "");
          if (ignoredInlineChunkRef.current && text === ignoredInlineChunkRef.current) {
            ignoredInlineChunkRef.current = null;
            return;
          }
          setMessages((current) => {
            const replyId = currentReplyId.current ?? crypto.randomUUID();
            currentReplyId.current = replyId;
            const placeholder = [...current]
              .reverse()
              .find((item) => item.role === "assistant" && item.content === "Thinking...");
            if (placeholder) {
              return current.map((item) =>
                item.id === placeholder.id ? { ...item, id: replyId, content: text } : item,
              );
            }
            const existing = current.find((item) => item.id === replyId);
            if (!existing) {
              return [...current, { id: replyId, role: "assistant", content: text }];
            }
            const existingContent = existing.content === "Thinking..." ? "" : existing.content;
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
        if (frame.type === "event" && frame.event === "agent.done") {
          setMessages((current) =>
            current.map((item) =>
              item.id === currentReplyId.current && item.content.trim() === ""
                ? { ...item, content: "(no response)" }
                : item,
            ),
          );
          currentReplyId.current = null;
          ignoredInlineChunkRef.current = null;
          pendingIdRef.current = null;
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
    pendingIdRef.current = requestId;
    const replyId = crypto.randomUUID();
    currentReplyId.current = replyId;
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
    <div className="space-y-4">
      <section className="rounded-[2rem] border border-white/80 bg-white/85 p-5 shadow-panel backdrop-blur">
        <div className="flex flex-col gap-4 border-b border-line/70 pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.28em] text-accent">Live Relay</p>
            <h1 className="mt-2 font-display text-4xl leading-none text-ink">SonarBot Console</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
              One continuous thread across tools, memory, and automation. Ask naturally, use slash commands, or trigger
              OAuth and browser tasks from the same panel.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <div
              className={`rounded-full px-3 py-1 text-xs ${
                socketReady ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"
              }`}
            >
              {socketReady ? "Realtime connected" : "Reconnecting"}
            </div>
            <div className="rounded-full bg-glow px-3 py-1 text-xs text-accent">Session webchat_main</div>
            <div className="rounded-full bg-sand px-3 py-1 text-xs text-slate-700">Transport WebSocket</div>
          </div>
        </div>

        <div
          ref={transcriptRef}
          className="mt-5 flex h-[68vh] flex-col gap-3 overflow-y-auto rounded-[1.75rem] border border-line/70 bg-gradient-to-b from-foam via-white to-white px-4 py-5"
        >
          {messages.length === 0 ? (
            <div className="rounded-[1.5rem] border border-dashed border-line/80 bg-white/80 p-5 text-sm text-slate-500">
              Your conversation will appear here. Try asking SonarBot about recent email, GitHub repos, or use
              <span className="mx-1 rounded bg-glow px-2 py-1 text-accent">/oauth-status</span>
              to inspect connected providers.
            </div>
          ) : null}
          {messages.map((message) => (
            <MessageBubble key={message.id} role={message.role} content={message.content} />
          ))}
        </div>

        <form onSubmit={onSubmit} className="mt-4 flex gap-3 rounded-[1.5rem] border border-line/70 bg-white/90 p-3">
          <input
            className="flex-1 rounded-2xl bg-transparent px-3 py-3 outline-none transition placeholder:text-slate-400"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask about Gmail, GitHub, memory, or try /skills"
          />
          <button
            className="rounded-2xl bg-accent px-5 py-3 text-sm font-medium text-white transition hover:bg-ink"
            type="submit"
          >
            Send
          </button>
        </form>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-[1.75rem] border border-white/80 bg-white/80 p-5 shadow-card">
          <p className="text-xs uppercase tracking-[0.24em] text-accent">Command Surface</p>
          <h2 className="mt-2 font-display text-2xl text-ink">Fast paths</h2>
          <ul className="mt-4 space-y-2 text-sm leading-6 text-slate-600">
            <li><span className="font-medium text-ink">/new</span> starts a fresh session thread</li>
            <li><span className="font-medium text-ink">/status</span> shows runtime and session details</li>
            <li><span className="font-medium text-ink">/oauth-status</span> lists connected providers</li>
            <li><span className="font-medium text-ink">/skills</span> shows enabled workflow skills</li>
          </ul>
        </div>
        <div className="rounded-[1.75rem] border border-white/80 bg-gradient-to-br from-ink to-accent p-5 text-white shadow-card">
          <p className="text-xs uppercase tracking-[0.24em] text-white/70">Connected Capabilities</p>
          <h2 className="mt-2 font-display text-2xl">What SonarBot can reach</h2>
          <p className="mt-3 text-sm leading-6 text-white/85">
            Gmail, GitHub, memory, browser automation, search, and delegated agent tasks all feed into this same live
            workspace.
          </p>
        </div>
      </section>
    </div>
  );
}
