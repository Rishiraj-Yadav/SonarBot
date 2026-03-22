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
  const deviceId = useMemo(() => getDeviceId(), []);

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
          }
        }
        if (frame.type === "event" && frame.event === "agent.chunk") {
          const text = String((frame.payload as Record<string, unknown> | undefined)?.text ?? "");
          setMessages((current) => {
            const replyId = currentReplyId.current ?? crypto.randomUUID();
            currentReplyId.current = replyId;
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
        if (frame.type === "event" && frame.event === "agent.done") {
          setMessages((current) =>
            current.map((item) =>
              item.id === currentReplyId.current && item.content.trim() === ""
                ? { ...item, content: "(no response)" }
                : item,
            ),
          );
          currentReplyId.current = null;
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
    <div className="grid gap-6 lg:grid-cols-[1.4fr_0.8fr]">
      <section className="rounded-[2rem] border border-line bg-white p-5 shadow-card">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-accent">Live Chat</p>
            <h1 className="text-3xl font-semibold">SonarBot</h1>
          </div>
          <div className={`rounded-full px-3 py-1 text-xs ${socketReady ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
            {socketReady ? "Connected" : "Connecting"}
          </div>
        </div>

        <div className="mb-4 flex h-[60vh] flex-col gap-3 overflow-y-auto rounded-3xl bg-mist/60 p-4">
          {messages.map((message) => (
            <MessageBubble key={message.id} role={message.role} content={message.content} />
          ))}
        </div>

        <form onSubmit={onSubmit} className="flex gap-3">
          <input
            className="flex-1 rounded-2xl border border-line px-4 py-3 outline-none transition focus:border-accent"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Send a message or try /skills"
          />
          <button className="rounded-2xl bg-accent px-5 py-3 text-white transition hover:bg-ink" type="submit">
            Send
          </button>
        </form>
      </section>

      <div className="space-y-6">
        <section className="rounded-3xl border border-line bg-white p-5 shadow-card">
          <p className="text-xs uppercase tracking-[0.2em] text-accent">Quick Notes</p>
          <h2 className="mt-2 text-2xl font-semibold">Command Surface</h2>
          <ul className="mt-4 space-y-2 text-sm text-slate-600">
            <li>/new starts a fresh session</li>
            <li>/status shows the current runtime state</li>
            <li>/memory prints long-term memory</li>
            <li>/skills lists enabled skills</li>
          </ul>
        </section>
      </div>
    </div>
  );
}
