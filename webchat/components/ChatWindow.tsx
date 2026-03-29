"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { MessageBubble } from "./MessageBubble";
import { createGatewaySocket, fetchJson } from "../lib/gateway_client";

type ChatMessage = {
  id: string;
  role: string;
  content: string;
  approvalId?: string;
  approvalStatus?: string;
  approvalActionKind?: string;
  approvalTargetSummary?: string;
  approvalCategory?: string;
};

type HistoryResponse = {
  messages: ChatMessage[];
};

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

const deviceKey = "sonarbot-webchat-device-id";

function generateId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}


function getDeviceId() {
  if (typeof window === "undefined") {
    return "webchat-local";
  }
  const existing = window.localStorage.getItem(deviceKey);
  if (existing) {
    return existing;
  }
  const created = generateId();
  window.localStorage.setItem(deviceKey, created);
  document.cookie = `sonarbot_webchat=${created}; path=/`;
  return created;
}

function getBrowserSpeechRecognitionCtor(): (new () => BrowserSpeechRecognition) | null {
  if (typeof window === "undefined") {
    return null;
  }
  const speechWindow = window as Window & {
    SpeechRecognition?: new () => BrowserSpeechRecognition;
    webkitSpeechRecognition?: new () => BrowserSpeechRecognition;
  };
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null;
}

export function ChatWindow() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [socketReady, setSocketReady] = useState(false);
  const [speechSupported, setSpeechSupported] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(true);
  const [micPanelOpen, setMicPanelOpen] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const listeningRequestIdRef = useRef<string | null>(null);
  const activeRequestIdRef = useRef<string | null>(null);
  const queuedRequestIdsRef = useRef<string[]>([]);
  const requestReplyMapRef = useRef<Map<string, string>>(new Map());
  const replyContentRef = useRef<Map<string, string>>(new Map());
  const ignoredInlineChunksRef = useRef<string[]>([]);
  const latestTranscriptRef = useRef("");
  const messagesRef = useRef<ChatMessage[]>([]);
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
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    // Always supported via the backend python process
    setSpeechSupported(true);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || !speakReplies) {
      return;
    }
    return () => {
      window.speechSynthesis.cancel();
    };
  }, [speakReplies]);

  function sendMessage(message: string) {
    const trimmed = message.trim();
    if (!trimmed || !socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      return;
    }
    if (typeof window !== "undefined" && speakReplies) {
      window.speechSynthesis.cancel();
    }
    const requestId = generateId();
    const replyId = generateId();
    requestReplyMapRef.current.set(requestId, replyId);
    replyContentRef.current.set(replyId, "");
    if (activeRequestIdRef.current === null) {
      activeRequestIdRef.current = requestId;
    } else {
      queuedRequestIdsRef.current.push(requestId);
    }
    setMessages((current) => [
      ...current,
      { id: requestId, role: "user", content: trimmed },
      { id: replyId, role: "assistant", content: "Thinking..." },
    ]);
    socketRef.current.send(
      JSON.stringify({
        type: "req",
        id: requestId,
        method: "agent.send",
        params: { message: trimmed },
      }),
    );
    setInput("");
  }

  async function decideHostApproval(approvalId: string, decision: "approved" | "rejected") {
    if (!approvalId) {
      return;
    }
    try {
      await fetch(`http://localhost:8765/api/system-access/approvals/${approvalId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      setMessages((current) =>
        current.map((item) =>
          item.approvalId === approvalId
            ? {
                ...item,
                approvalStatus: decision,
                content: decision === "approved" ? "Host action approved." : "Host action denied.",
              }
            : item,
        ),
      );
    } catch {
      setMessages((current) =>
        current.map((item) =>
          item.approvalId === approvalId
            ? { ...item, content: "I couldn't update that approval. Please try again from Host Access." }
            : item,
        ),
      );
    }
  }

  function speakMessage(text: string) {
    if (typeof window === "undefined" || !speakReplies || !text.trim()) {
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text.trim());
    utterance.rate = 1;
    utterance.pitch = 1;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  }

  function applyDictationText(text: string) {
    const trimmed = text.trim();
    setInput(trimmed);
    setLiveTranscript(trimmed);
  }

  function stopRecognition() {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      try {
        recognition.onresult = null;
        recognition.onerror = null;
        recognition.onend = null;
        recognition.abort();
      } catch {
        // Ignore cleanup failures when the browser already shut recognition down.
      }
    }
  }

  function startBrowserMic() {
    const RecognitionCtor = getBrowserSpeechRecognitionCtor();
    if (!RecognitionCtor) {
      return false;
    }

    stopRecognition();
    const recognition = new RecognitionCtor();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognitionRef.current = recognition;

    let finalTranscript = "";

    recognition.onresult = (event: any) => {
      let interimTranscript = "";
      const results = event?.results ?? [];
      for (let index = event.resultIndex ?? 0; index < results.length; index += 1) {
        const result = results[index];
        const transcript = String(result?.[0]?.transcript ?? "");
        if (!transcript) {
          continue;
        }
        if (result?.isFinal) {
          finalTranscript += transcript;
        } else {
          interimTranscript += transcript;
        }
      }
      const combined = `${finalTranscript} ${interimTranscript}`.trim();
      setLiveTranscript(combined || "Listening...");
    };

    recognition.onerror = (event: any) => {
      const errorName = String(event?.error ?? "unknown_error");
      setLiveTranscript(`Mic error: ${errorName}`);
      setIsListening(false);
      setMicPanelOpen(false);
      stopRecognition();
    };

    recognition.onend = () => {
      recognitionRef.current = null;
      setIsListening(false);
      setMicPanelOpen(false);
      const text = finalTranscript.trim();
      if (text) {
        applyDictationText(text);
      } else {
        setLiveTranscript("");
      }
    };

    setIsListening(true);
    setMicPanelOpen(true);
    setLiveTranscript("Listening via browser microphone...");

    try {
      recognition.start();
    } catch (error) {
      recognitionRef.current = null;
      setIsListening(false);
      setMicPanelOpen(false);
      setLiveTranscript(`Mic error: ${error instanceof Error ? error.message : "failed to start"}`);
      return false;
    }

    return true;
  }

  function toggleListening() {
    if (isListening) {
      stopRecognition();
      setIsListening(false);
      setMicPanelOpen(false);
      setLiveTranscript("");
      return;
    }

    if (startBrowserMic()) {
      return;
    }

    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      alert("Mic input needs a browser mic or a connected assistant backend.");
      return;
    }

    setIsListening(true);
    setMicPanelOpen(true);
    setLiveTranscript("Establishing secure backend mic link...");
    const requestId = generateId();
    listeningRequestIdRef.current = requestId;
    activeRequestIdRef.current = requestId;

    socketRef.current.send(
      JSON.stringify({
        type: "req",
        id: requestId,
        method: "agent.listen",
        params: { session_key: "main" },
      }),
    );
  }

  function closeMicPanel() {
    listeningRequestIdRef.current = null;
    stopRecognition();
    setIsListening(false);
    setMicPanelOpen(false);
    setLiveTranscript("");
    latestTranscriptRef.current = input.trim();
  }

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
          const replyId = requestReplyMapRef.current.get(requestId);
          if (replyId) {
            replyContentRef.current.delete(replyId);
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
          if (listeningRequestIdRef.current && requestId === listeningRequestIdRef.current) {
            const payload = (frame.payload as Record<string, unknown> | undefined) ?? {};
            const transcript = typeof payload.transcript === "string" ? payload.transcript : "";
            if (transcript) {
              applyDictationText(transcript);
            }
            setIsListening(false);
            setMicPanelOpen(false);
            if (!transcript) {
              setLiveTranscript(String(payload.error ?? payload.command_response ?? "No speech detected"));
            } else {
              setLiveTranscript("");
            }
            listeningRequestIdRef.current = null;
            activeRequestIdRef.current = null;
            cleanupRequest(requestId);
            return;
          }
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
            if (replyId) {
              replyContentRef.current.set(replyId, commandResponse);
            }
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
          const previousContent = replyContentRef.current.get(replyId) ?? "";
          const nextContent = previousContent === "" || previousContent === "Thinking..." ? text : previousContent + text;
          replyContentRef.current.set(replyId, nextContent);
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
        if (frame.type === "event" && frame.event === "agent.mic_active") {
          setLiveTranscript("Listening via Python backend...");
        }
        if (frame.type === "event" && frame.event === "agent.mic_inactive") {
          setIsListening(false);
          setMicPanelOpen(false);
          const text = String((frame.payload as Record<string, unknown> | undefined)?.text ?? "");
          if (text.trim()) {
            applyDictationText(text);
          } else {
            setLiveTranscript("");
          }
          listeningRequestIdRef.current = null;
          const lastId = activeRequestIdRef.current;
          if (lastId) {
            cleanupRequest(lastId);
            activeRequestIdRef.current = null;
          }
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
          const payload = (frame.payload as Record<string, unknown> | undefined) ?? {};
          if (frame.event === "host_approval.created") {
            const approvalId = String(payload.approval_id ?? "");
            const targetSummary = String(payload.target_summary ?? "Unknown host action");
            const category = String(payload.category ?? "ask_once");
            if (approvalId) {
              setMessages((prev) => [
                ...prev,
                {
                  id: approvalId,
                  role: "system",
                  content: "Host action needs your approval.",
                  approvalId,
                  approvalStatus: "pending",
                  approvalActionKind: "host action",
                  approvalTargetSummary: targetSummary,
                  approvalCategory: category,
                },
              ]);
            }
          } else {
            const approvalId = String(payload.approval_id ?? "");
            const status = String(payload.status ?? "");
            if (approvalId && status) {
              setMessages((current) =>
                current.map((item) =>
                  item.approvalId === approvalId
                    ? {
                        ...item,
                        approvalStatus: status,
                        content:
                          status === "approved"
                            ? "Host action approved."
                            : status === "rejected"
                              ? "Host action denied."
                              : item.content,
                      }
                    : item,
                ),
              );
            }
          }
          if (typeof window !== "undefined") {
            window.dispatchEvent(
              new CustomEvent("sonarbot:host-approval", {
                detail: payload,
              }),
            );
          }
        }
        if (frame.type === "event" && frame.event === "agent.done") {
          const requestId = activeRequestIdRef.current;
          const replyId = currentReplyId();
          const finalContent = replyId ? (replyContentRef.current.get(replyId) ?? "") : "";
          const resolvedContent =
            finalContent.trim() && finalContent !== "Thinking..." ? finalContent : "(no response)";
          setMessages((current) =>
            current.map((item) => {
              if (item.id !== replyId) {
                return item;
              }
              if (item.content.trim() === "" || item.content === "Thinking...") {
                return { ...item, content: resolvedContent };
              }
              return item;
            }),
          );
          if (replyId) {
            speakMessage(resolvedContent);
          }
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
      stopRecognition();
      socketRef.current?.close();
    };
  }, [deviceId]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    sendMessage(input);
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
            <div className="mt-1">{speechSupported ? "Voice + text" : "Slash + natural"}</div>
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
        {messages.map((message) =>
          message.approvalId ? (
            <div
              key={message.id}
              className="max-w-[90%] rounded-[1.65rem] border border-line bg-sand/80 px-4 py-4 shadow-card text-ink"
            >
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="text-[11px] uppercase tracking-[0.24em] opacity-70">{message.role}</div>
                <div className="rounded-full bg-white/60 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-slate-600">
                  Approval
                </div>
              </div>
              <div className="text-sm leading-7">
                <div className="font-medium text-ink">{message.content}</div>
                <div className="mt-2 text-slate-600">{message.approvalTargetSummary}</div>
                {message.approvalStatus === "pending" ? (
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void decideHostApproval(message.approvalId!, "approved")}
                      className="rounded-full bg-emerald-100 px-3 py-2 text-xs font-medium text-emerald-700"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => void decideHostApproval(message.approvalId!, "rejected")}
                      className="rounded-full bg-rose-100 px-3 py-2 text-xs font-medium text-rose-700"
                    >
                      Deny
                    </button>
                  </div>
                ) : (
                  <div className="mt-4 text-[11px] uppercase tracking-[0.18em] text-slate-500">
                    {message.approvalStatus}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <MessageBubble key={message.id} role={message.role} content={message.content} />
          ),
        )}
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
          {speechSupported ? (
            <button
              className={`rounded-[1.25rem] px-6 py-3 text-sm font-medium transition ${
                isListening
                  ? "bg-rose-600 text-white hover:bg-rose-700"
                  : "border border-line/70 bg-white text-slate-700 hover:border-accent hover:text-accent"
              }`}
              type="button"
              onClick={toggleListening}
            >
              {isListening ? "Stop mic" : "Use mic"}
            </button>
          ) : null}
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-slate-600">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={speakReplies}
              onChange={(event) => {
                setSpeakReplies(event.target.checked);
                if (!event.target.checked && typeof window !== "undefined") {
                  window.speechSynthesis.cancel();
                }
              }}
            />
            Speak replies aloud
          </label>
          {speechSupported ? (
            <span>{isListening ? "Listening for your next message..." : "Voice input ready."}</span>
          ) : (
            <span>Voice input is not supported in this browser.</span>
          )}
        </div>
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
      {micPanelOpen ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-6 z-40 flex justify-center px-4">
          <div
            className="pointer-events-auto w-auto min-w-56 rounded-full border border-white/80 bg-white/92 px-5 py-3 shadow-2xl backdrop-blur-xl"
          >
            <div className="flex items-center gap-4">
              <button
                type="button"
                onClick={toggleListening}
                className={`relative flex h-12 w-12 items-center justify-center rounded-full text-white shadow-lg transition ${
                  isListening ? "bg-rose-600 hover:bg-rose-700" : "bg-accent hover:bg-ink"
                }`}
              >
                {isListening ? (
                  <>
                    <span className="voice-ring absolute inset-0 rounded-full border border-rose-300/70" />
                    <span className="voice-ring voice-ring-delay absolute inset-0 rounded-full border border-rose-200/60" />
                  </>
                ) : null}
                <span className="voice-orb-shadow absolute inset-1 rounded-full bg-white/12 blur-md" />
                <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-white/16 text-[10px] font-semibold tracking-[0.16em]">
                  {isListening ? "ON" : "MIC"}
                </span>
              </button>
              <div className="flex flex-col items-center gap-2">
                <div className="voice-bars" aria-hidden="true">
                  <span className={isListening ? "voice-bar active" : "voice-bar"} />
                  <span className={isListening ? "voice-bar active delay-1" : "voice-bar"} />
                  <span className={isListening ? "voice-bar active delay-2" : "voice-bar"} />
                  <span className={isListening ? "voice-bar active delay-3" : "voice-bar"} />
                  <span className={isListening ? "voice-bar active delay-4" : "voice-bar"} />
                </div>
                <p className="max-w-40 text-center text-xs text-slate-500">
                  {isListening ? "Listening. Stop manually when you are done." : "Mic paused."}
                </p>
              </div>
              <button
                type="button"
                onClick={closeMicPanel}
                className="rounded-full border border-line/70 px-4 py-2 text-xs text-slate-600 transition hover:border-accent hover:text-accent"
              >
                Hide
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
