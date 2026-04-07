"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { MessageBubble } from "./MessageBubble";
import { createGatewaySocket, fetchJson, synthesizeVoiceReply, transcribeVoiceClip } from "../lib/gateway_client";

type ChatMessage = {
  id: string;
  role: string;
  content: string;
};

type HistoryResponse = {
  messages: ChatMessage[];
};

type SettingsResponse = {
  voice?: {
    enabled?: boolean;
    webchat_enabled?: boolean;
    webchat_tts_enabled?: boolean;
    auto_send_transcript?: boolean;
    max_record_seconds?: number;
  };
};

type PendingSend = {
  requestId: string;
  replyId: string;
  message: string;
  metadata: Record<string, unknown>;
  isVoice: boolean;
};

const deviceKey = "sonarbot-webchat-device-id";
const voicePreferenceKey = "sonarbot-webchat-voice-enabled";
const voiceStartThreshold = 0.06;
const voiceStopThreshold = 0.045;
const voiceRequiredFrames = 1;
const voiceMinRecordingMs = 900;
const voiceSilenceMs = 1100;

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

function pickRecorderMimeType(): string {
  if (typeof window === "undefined" || typeof MediaRecorder === "undefined") {
    return "audio/webm";
  }
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
  for (const candidate of candidates) {
    if (MediaRecorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }
  return "audio/webm";
}

export function ChatWindow() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [inputFromVoice, setInputFromVoice] = useState(false);
  const [socketReady, setSocketReady] = useState(false);
  const [backendVoiceEnabled, setBackendVoiceEnabled] = useState(false);
  const [backendVoiceReplyEnabled, setBackendVoiceReplyEnabled] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceReplyEnabled, setVoiceReplyEnabled] = useState(false);
  const [autoSendTranscript, setAutoSendTranscript] = useState(true);
  const [maxRecordSeconds, setMaxRecordSeconds] = useState(60);
  const [isListening, setIsListening] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [isSpeakingReply, setIsSpeakingReply] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const [waveformBars, setWaveformBars] = useState<number[]>(() => Array.from({ length: 8 }, () => 4));
  const [silenceProgress, setSilenceProgress] = useState(0);
  const [showSentFlash, setShowSentFlash] = useState(false);
  const [lastTranscript, setLastTranscript] = useState("");
  const [lastTranscriptConfidence, setLastTranscriptConfidence] = useState<number | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const activeRequestIdRef = useRef<string | null>(null);
  const queuedRequestIdsRef = useRef<string[]>([]);
  const requestReplyMapRef = useRef<Map<string, string>>(new Map());
  const ignoredInlineChunksRef = useRef<string[]>([]);
  const replyContentRef = useRef<Map<string, string>>(new Map());
  const voiceRequestIdsRef = useRef<Set<string>>(new Set());
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const listeningStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const recordStartedAtRef = useRef<number | null>(null);
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioLevelTimeRef = useRef<Uint8Array | null>(null);
  const audioLevelFreqRef = useRef<Uint8Array | null>(null);
  const levelFrameRef = useRef<number | null>(null);
  const detectionTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loudFrameCountRef = useRef(0);
  const lastSoundAtRef = useRef(0);
  const discardRecordingRef = useRef(false);
  const spaceHoldActiveRef = useRef(false);
  const sentFlashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rootSectionRef = useRef<HTMLElement | null>(null);
  const isListeningRef = useRef(false);
  const isRecordingRef = useRef(false);
  const isTranscribingRef = useRef(false);
  const isSpeakingReplyRef = useRef(false);
  const voiceEnabledRef = useRef(false);
  const voiceReplyEnabledRef = useRef(false);
  const activeAudioUrlRef = useRef<string | null>(null);
  const pendingSendsRef = useRef<PendingSend[]>([]);
  const deviceId = useMemo(() => getDeviceId(), []);
  const quickPrompts = [
    { label: "Check inbox", value: "check my inbox" },
    { label: "/skills", value: "/skills" },
    { label: "Open browser profile", value: "open github in the browser" },
    { label: "/cron list", value: "/cron list" },
  ];

  useEffect(() => {
    voiceEnabledRef.current = voiceEnabled;
  }, [voiceEnabled]);

  useEffect(() => {
    isListeningRef.current = isListening;
  }, [isListening]);

  useEffect(() => {
    isRecordingRef.current = isRecording;
  }, [isRecording]);

  useEffect(() => {
    isTranscribingRef.current = isTranscribing;
  }, [isTranscribing]);

  useEffect(() => {
    isSpeakingReplyRef.current = isSpeakingReply;
  }, [isSpeakingReply]);

  useEffect(() => {
    voiceReplyEnabledRef.current = voiceReplyEnabled;
  }, [voiceReplyEnabled]);

  useEffect(() => {
    return () => {
      if (sentFlashTimerRef.current) {
        clearTimeout(sentFlashTimerRef.current);
        sentFlashTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat) {
        return;
      }
      if (!voiceEnabledRef.current || !isListeningRef.current || isTranscribingRef.current || isRecordingRef.current) {
        return;
      }
      const activeElement = document.activeElement;
      const isTypingField =
        activeElement?.tagName === "INPUT" ||
        activeElement?.tagName === "TEXTAREA" ||
        Boolean((activeElement as HTMLElement | null)?.isContentEditable);
      if (isTypingField) {
        return;
      }
      if (activeElement !== document.body && !rootSectionRef.current?.contains(activeElement)) {
        return;
      }
      event.preventDefault();
      spaceHoldActiveRef.current = true;
      void startSegmentRecording();
    };

    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space") {
        return;
      }
      if (!spaceHoldActiveRef.current) {
        return;
      }
      event.preventDefault();
      spaceHoldActiveRef.current = false;
      stopSegmentRecording();
    };

    if (!voiceEnabled || !isListening || isTranscribing) {
      return;
    }

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [voiceEnabled, isListening, isTranscribing]);

  const applyVoiceSettings = (payload: SettingsResponse) => {
    const voice = payload.voice ?? {};
    const serverVoiceEnabled = Boolean(voice.enabled && voice.webchat_enabled);
    const serverVoiceReplyEnabled = Boolean(serverVoiceEnabled && voice.webchat_tts_enabled);
    let preferredVoiceEnabled = serverVoiceEnabled;
    if (typeof window !== "undefined") {
      const savedPreference = window.localStorage.getItem(voicePreferenceKey);
      if (savedPreference === "true") {
        preferredVoiceEnabled = true;
      } else if (savedPreference === "false") {
        preferredVoiceEnabled = false;
      }
    }
    setBackendVoiceEnabled(serverVoiceEnabled);
    setBackendVoiceReplyEnabled(serverVoiceReplyEnabled);
    setVoiceEnabled(Boolean(serverVoiceEnabled && preferredVoiceEnabled));
    setAutoSendTranscript(voice.auto_send_transcript !== false);
    setMaxRecordSeconds(Math.max(5, Number(voice.max_record_seconds ?? 60)));
    if (serverVoiceEnabled) {
      setVoiceError((current) => (current.startsWith("Voice is disabled in backend settings") ? "" : current));
    }
    return serverVoiceEnabled;
  };

  const refreshVoiceSettings = async () => {
    const payload = await fetchJson<SettingsResponse>("/api/settings");
    return applyVoiceSettings(payload);
  };

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript) {
      return;
    }
    transcript.scrollTo({ top: transcript.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    void refreshVoiceSettings().catch(() => undefined);
  }, []);

  useEffect(() => {
    setVoiceReplyEnabled(Boolean(voiceEnabled && backendVoiceReplyEnabled));
  }, [voiceEnabled, backendVoiceReplyEnabled]);

  function dispatchPendingSend(entry: PendingSend): boolean {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    socket.send(
      JSON.stringify({
        type: "req",
        id: entry.requestId,
        method: "agent.send",
        params: { message: entry.message, metadata: entry.metadata },
      }),
    );
    return true;
  }

  function flushPendingSends() {
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      return;
    }
    const stillPending: PendingSend[] = [];
    for (const entry of pendingSendsRef.current) {
      if (!dispatchPendingSend(entry)) {
        stillPending.push(entry);
      }
    }
    pendingSendsRef.current = stillPending;
  }

  function clamp(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
  }

  function readTimeDomainLevel(): number {
    const analyser = analyserRef.current;
    const data = audioLevelTimeRef.current;
    if (!analyser || !data) {
      return 0;
    }
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let index = 0; index < data.length; index += 1) {
      const centered = (data[index] - 128) / 128;
      sum += centered * centered;
    }
    const rms = Math.sqrt(sum / data.length);
    return clamp(rms * 3.5, 0, 1);
  }

  function readFrequencyBars(): number[] {
    const analyser = analyserRef.current;
    const data = audioLevelFreqRef.current;
    const bars = Array.from({ length: 8 }, () => 4);
    if (!analyser || !data) {
      return bars;
    }
    analyser.getByteFrequencyData(data);
    const totalBins = data.length;
    const maxBin = Math.max(8, Math.floor(totalBins * 0.45));
    const minHeight = 4;
    const maxHeight = 32;
    for (let barIndex = 0; barIndex < bars.length; barIndex += 1) {
      const startBin = Math.floor((barIndex / bars.length) * maxBin);
      const endBin = Math.max(startBin + 1, Math.floor(((barIndex + 1) / bars.length) * maxBin));
      let sum = 0;
      let count = 0;
      for (let bin = startBin; bin < endBin; bin += 1) {
        sum += data[bin] ?? 0;
        count += 1;
      }
      const average = count > 0 ? sum / count / 255 : 0;
      const shaped = clamp(Math.pow(average * 1.9, 0.9), 0, 1);
      bars[barIndex] = minHeight + Math.round(shaped * (maxHeight - minHeight));
    }
    return bars;
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
        flushPendingSends();
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
          voiceRequestIdsRef.current.delete(requestId);
          if (activeRequestIdRef.current === requestId) {
            activeRequestIdRef.current = queuedRequestIdsRef.current.shift() ?? null;
            return;
          }
          queuedRequestIdsRef.current = queuedRequestIdsRef.current.filter((item) => item !== requestId);
        };

        const updateReplyContent = (replyId: string | null, content: string) => {
          if (!replyId) {
            return;
          }
          replyContentRef.current.set(replyId, content);
        };

        if (frame.type === "res") {
          const requestId = String(frame.id ?? "");
          const ok = Boolean(frame.ok);
          if (!ok) {
            const errorText = String(frame.error ?? "Unknown request error");
            const replyId = replyIdForRequest(requestId);
            setMessages((current) =>
              current.map((item) => (item.id === replyId ? { ...item, content: `[Error] ${errorText}` } : item)),
            );
            updateReplyContent(replyId, `[Error] ${errorText}`);
            cleanupRequest(requestId);
            return;
          }

          const payload = (frame.payload as Record<string, unknown> | undefined) ?? undefined;
          const queued = Boolean(payload?.queued);
          const commandResponse = typeof payload?.command_response === "string" ? payload.command_response : null;
          if (commandResponse) {
            ignoredInlineChunksRef.current.push(commandResponse);
            const replyId = replyIdForRequest(requestId);
            updateReplyContent(replyId, commandResponse);
            setMessages((current) => {
              const placeholder = [...current].reverse().find((item) => item.id === replyId && item.role === "assistant");
              if (placeholder) {
                return current.map((item) =>
                  item.id === placeholder.id ? { ...item, content: commandResponse } : item,
                );
              }
              const existing = current.find((item) => item.id === replyId);
              if (existing) {
                return current.map((item) => (item.id === replyId ? { ...item, content: commandResponse } : item));
              }
              return replyId ? [...current, { id: replyId, role: "assistant", content: commandResponse }] : current;
            });
            void maybeSpeakReply(requestId, replyId);
          }
          if (!queued || commandResponse) {
            cleanupRequest(requestId);
          }
          return;
        }

        if (frame.type === "event" && frame.event === "agent.chunk") {
          const text = String((frame.payload as Record<string, unknown> | undefined)?.text ?? "");
          const ignoredIndex = ignoredInlineChunksRef.current.indexOf(text);
          if (ignoredIndex >= 0) {
            ignoredInlineChunksRef.current.splice(ignoredIndex, 1);
            return;
          }
          const requestId = activeRequestIdRef.current;
          const replyId = requestId ? requestReplyMapRef.current.get(requestId) ?? null : null;
          if (!replyId) {
            return;
          }
          setMessages((current) => {
            const existing = current.find((item) => item.id === replyId);
            if (!existing) {
              updateReplyContent(replyId, text);
              return [...current, { id: replyId, role: "assistant", content: text }];
            }
            const updated = existing.content === "Thinking..." ? text : existing.content + text;
            updateReplyContent(replyId, updated);
            return current.map((item) => (item.id === replyId ? { ...item, content: updated } : item));
          });
          return;
        }

        if (frame.type === "event" && frame.event === "notification.created") {
          const detail = (frame.payload as Record<string, unknown> | undefined) ?? {};
          const notificationId = String(detail.notification_id ?? crypto.randomUUID());
          const title = String(detail.title ?? "Automation update").trim();
          const body = String(detail.body ?? "").trim();
          const content = body && body !== title ? `${title}\n\n${body}` : title;
          if (content) {
            setMessages((current) => {
              const messageId = `notification-${notificationId}`;
              if (current.some((item) => item.id === messageId)) {
                return current;
              }
              return [...current, { id: messageId, role: "assistant", content }];
            });
          }
          if (typeof window !== "undefined") {
            window.dispatchEvent(new CustomEvent("sonarbot:notification", { detail }));
          }
          return;
        }

        if (frame.type === "event" && frame.event === "report.message") {
          const detail = (frame.payload as Record<string, unknown> | undefined) ?? {};
          const body = String(detail.body ?? "").trim();
          if (body) {
            setMessages((current) => [...current, { id: crypto.randomUUID(), role: "assistant", content: body }]);
          }
          return;
        }

        if (frame.type === "event" && typeof frame.event === "string" && frame.event.startsWith("browser.")) {
          if (typeof window !== "undefined") {
            window.dispatchEvent(
              new CustomEvent(`sonarbot:${frame.event}`, {
                detail: (frame.payload as Record<string, unknown> | undefined) ?? {},
              }),
            );
          }
          return;
        }

        if (frame.type === "event" && (frame.event === "host_approval.created" || frame.event === "host_approval.updated")) {
          if (typeof window !== "undefined") {
            window.dispatchEvent(
              new CustomEvent("sonarbot:host-approval", {
                detail: (frame.payload as Record<string, unknown> | undefined) ?? {},
              }),
            );
          }
          return;
        }

        if (frame.type === "event" && frame.event === "agent.done") {
          const requestId = activeRequestIdRef.current;
          const replyId = requestId ? requestReplyMapRef.current.get(requestId) ?? null : null;
          setMessages((current) =>
            current.map((item) =>
              item.id === replyId && item.content.trim() === "" ? { ...item, content: "(no response)" } : item,
            ),
          );
          void maybeSpeakReply(requestId, replyId);
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
      void stopVoiceSession({ disableVoice: false, discardRecording: true });
      if (activeAudioUrlRef.current) {
        URL.revokeObjectURL(activeAudioUrlRef.current);
        activeAudioUrlRef.current = null;
      }
      socketRef.current?.close();
    };
  }, [deviceId]);

  useEffect(() => {
    if (!voiceEnabled) {
      void stopVoiceSession({ disableVoice: false, discardRecording: true });
      return;
    }
    void ensureVoiceSession();
  }, [voiceEnabled]);

  function currentLevel(): number {
    return readTimeDomainLevel();
  }

  function startLevelAnimation() {
    if (levelFrameRef.current !== null) {
      cancelAnimationFrame(levelFrameRef.current);
    }
    const tick = () => {
      if (!voiceEnabledRef.current || !isListeningRef.current) {
        setWaveformBars(Array.from({ length: 8 }, () => 4));
      } else {
        const nextBars = readFrequencyBars();
        setWaveformBars((current) =>
          current.map((value, index) => {
            const next = nextBars[index] ?? 4;
            return Math.round(value * 0.7 + next * 0.3);
          }),
        );
      }
      levelFrameRef.current = requestAnimationFrame(tick);
    };
    levelFrameRef.current = requestAnimationFrame(tick);
  }

  function stopLevelAnimation() {
    if (levelFrameRef.current !== null) {
      cancelAnimationFrame(levelFrameRef.current);
      levelFrameRef.current = null;
    }
    setWaveformBars(Array.from({ length: 8 }, () => 4));
  }

  function voiceDetectorTick() {
    if (!voiceEnabledRef.current || !isListeningRef.current) {
      setSilenceProgress(0);
      return;
    }
    const level = currentLevel();
    const now = Date.now();
    const recorder = mediaRecorderRef.current;
    const agentBusy = activeRequestIdRef.current !== null || isTranscribingRef.current || isSpeakingReplyRef.current;

    if (recorder && recorder.state === "recording") {
      if (level >= voiceStopThreshold) {
        lastSoundAtRef.current = now;
        setSilenceProgress(0);
      }
      const recordingStartedAt = recordStartedAtRef.current ?? now;
      const elapsed = now - recordingStartedAt;
      if (elapsed >= maxRecordSeconds * 1000) {
        setSilenceProgress(0);
        stopSegmentRecording();
        return;
      }
      const silenceElapsed = now - lastSoundAtRef.current;
      if (elapsed >= voiceMinRecordingMs && silenceElapsed > 600) {
        const remaining = Math.max(1, voiceSilenceMs - 600);
        const nextProgress = clamp((silenceElapsed - 600) / remaining, 0, 1);
        setSilenceProgress(nextProgress);
      } else {
        setSilenceProgress(0);
      }
      if (elapsed >= voiceMinRecordingMs && silenceElapsed >= voiceSilenceMs) {
        setSilenceProgress(0);
        stopSegmentRecording();
      }
      return;
    }

    if (agentBusy) {
      loudFrameCountRef.current = 0;
      setSilenceProgress(0);
      return;
    }

    setSilenceProgress(0);

    if (level >= voiceStartThreshold) {
      loudFrameCountRef.current += 1;
      lastSoundAtRef.current = now;
    } else {
      loudFrameCountRef.current = 0;
    }

    if (loudFrameCountRef.current >= voiceRequiredFrames) {
      loudFrameCountRef.current = 0;
      void startSegmentRecording();
    }
  }

  async function ensureVoiceSession() {
    if (listeningStreamRef.current) {
      setIsListening(true);
      return;
    }
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setVoiceError("Voice recording is not supported in this browser.");
      setVoiceEnabled(false);
      return;
    }
    try {
      setVoiceError("");
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      listeningStreamRef.current = stream;
      const audioContext = new AudioContext();
      audioContextRef.current = audioContext;
      const sourceNode = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.8;
      sourceNode.connect(analyser);
      analyserRef.current = analyser;
      audioLevelTimeRef.current = new Uint8Array(analyser.fftSize);
      audioLevelFreqRef.current = new Uint8Array(analyser.frequencyBinCount);
      setIsListening(true);
      startLevelAnimation();
      if (detectionTimerRef.current !== null) {
        clearInterval(detectionTimerRef.current);
      }
      detectionTimerRef.current = setInterval(voiceDetectorTick, 140);
    } catch (error) {
      const errorName = error instanceof Error ? error.name : "";
      if (errorName === "NotAllowedError" || errorName === "PermissionDeniedError") {
        setVoiceError(
          "Microphone access was denied. Please allow microphone access in your browser settings and try again.",
        );
      } else if (errorName === "NotFoundError") {
        setVoiceError("No microphone was found. Please connect a microphone and try again.");
      } else {
        setVoiceError(error instanceof Error ? error.message : "Unable to access the microphone.");
      }
      setVoiceEnabled(false);
    }
  }

  async function stopVoiceSession(options?: { disableVoice?: boolean; discardRecording?: boolean }) {
    if (options?.discardRecording) {
      discardRecordingRef.current = true;
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    if (detectionTimerRef.current !== null) {
      clearInterval(detectionTimerRef.current);
      detectionTimerRef.current = null;
    }
    stopLevelAnimation();
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    if (sentFlashTimerRef.current) {
      clearTimeout(sentFlashTimerRef.current);
      sentFlashTimerRef.current = null;
    }
    listeningStreamRef.current?.getTracks().forEach((track) => track.stop());
    listeningStreamRef.current = null;
    try {
      await audioContextRef.current?.close();
    } catch {
      // Ignore audio context cleanup errors.
    }
    audioContextRef.current = null;
    analyserRef.current = null;
    audioLevelTimeRef.current = null;
    audioLevelFreqRef.current = null;
    loudFrameCountRef.current = 0;
    setSilenceProgress(0);
    setShowSentFlash(false);
    spaceHoldActiveRef.current = false;
    setIsListening(false);
    setIsRecording(false);
    if (options?.disableVoice) {
      setVoiceEnabled(false);
    }
  }

  async function startSegmentRecording() {
    if (!listeningStreamRef.current || mediaRecorderRef.current?.state === "recording" || isTranscribingRef.current) {
      return;
    }
    try {
      const mimeType = pickRecorderMimeType();
      const recorder = new MediaRecorder(listeningStreamRef.current, mimeType ? { mimeType } : undefined);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];
      discardRecordingRef.current = false;
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        const durationMs = recordStartedAtRef.current ? Math.max(1, Date.now() - recordStartedAtRef.current) : 0;
        const blob = new Blob(audioChunksRef.current, { type: recorder.mimeType || mimeType || "audio/webm" });
        audioChunksRef.current = [];
        recordStartedAtRef.current = null;
        mediaRecorderRef.current = null;
        spaceHoldActiveRef.current = false;
        setIsRecording(false);
        setSilenceProgress(0);
        if (stopTimerRef.current) {
          clearTimeout(stopTimerRef.current);
          stopTimerRef.current = null;
        }
        if (discardRecordingRef.current) {
          discardRecordingRef.current = false;
          return;
        }
        if (blob.size > 0) {
          void uploadRecordedAudio(blob, durationMs);
        }
      };
      recordStartedAtRef.current = Date.now();
      lastSoundAtRef.current = Date.now();
      setSilenceProgress(0);
      recorder.start();
      setIsRecording(true);
      stopTimerRef.current = setTimeout(() => stopSegmentRecording(), maxRecordSeconds * 1000);
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : "Unable to start recording.");
    }
  }

  function stopSegmentRecording(discard = false) {
    if (discard) {
      discardRecordingRef.current = true;
    }
    spaceHoldActiveRef.current = false;
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    } else {
      setIsRecording(false);
      setSilenceProgress(0);
    }
  }

  async function uploadRecordedAudio(blob: Blob, durationMs: number) {
    setIsTranscribing(true);
    setVoiceError("");
    try {
      const transcript = await transcribeVoiceClip(blob, durationMs, deviceId);
      const transcriptText = transcript.text.trim();
      if (!transcriptText) {
        setVoiceError("I couldn't detect any speech in that recording.");
        setShowSentFlash(false);
        return;
      }
      setLastTranscriptConfidence(Number(transcript.confidence ?? 0));
      setLastTranscript(transcriptText);
      if (autoSendTranscript) {
        sendMessage(transcriptText, { metadata: transcript.metadata, isVoice: true });
      } else {
        setInput(transcriptText);
        setInputFromVoice(true);
      }
      if (sentFlashTimerRef.current) {
        clearTimeout(sentFlashTimerRef.current);
      }
      setShowSentFlash(true);
      sentFlashTimerRef.current = setTimeout(() => setShowSentFlash(false), 1000);
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : "Voice transcription failed.");
      setShowSentFlash(false);
    } finally {
      setIsTranscribing(false);
    }
  }

  async function maybeSpeakReply(requestId: string | null | undefined, replyId: string | null | undefined) {
    if (!requestId || !replyId || !voiceReplyEnabledRef.current || !voiceRequestIdsRef.current.has(requestId)) {
      return;
    }
    const replyText = replyContentRef.current.get(replyId)?.trim() ?? "";
    if (!replyText || replyText.startsWith("[Error]")) {
      return;
    }
    try {
      setIsSpeakingReply(true);
      const audioBlob = await synthesizeVoiceReply(replyText);
      if (activeAudioUrlRef.current) {
        URL.revokeObjectURL(activeAudioUrlRef.current);
      }
      const objectUrl = URL.createObjectURL(audioBlob);
      activeAudioUrlRef.current = objectUrl;
      const audio = new Audio(objectUrl);
      audio.onended = () => {
        URL.revokeObjectURL(objectUrl);
        if (activeAudioUrlRef.current === objectUrl) {
          activeAudioUrlRef.current = null;
        }
        setIsSpeakingReply(false);
      };
      audio.onerror = () => {
        setIsSpeakingReply(false);
      };
      await audio.play();
    } catch {
      setIsSpeakingReply(false);
    }
  }

  function sendMessage(messageText: string, options?: { metadata?: Record<string, unknown>; isVoice?: boolean }) {
    const normalized = messageText.trim();
    if (!normalized) {
      return false;
    }
    const requestId = crypto.randomUUID();
    const replyId = crypto.randomUUID();
    requestReplyMapRef.current.set(requestId, replyId);
    if (activeRequestIdRef.current === null) {
      activeRequestIdRef.current = requestId;
    } else {
      queuedRequestIdsRef.current.push(requestId);
    }
    if (options?.isVoice) {
      voiceRequestIdsRef.current.add(requestId);
    }
    replyContentRef.current.set(replyId, "Thinking...");
    setMessages((current) => [
      ...current,
      { id: requestId, role: "user", content: normalized },
      { id: replyId, role: "assistant", content: "Thinking..." },
    ]);
    const pending: PendingSend = {
      requestId,
      replyId,
      message: normalized,
      metadata: options?.metadata ?? {},
      isVoice: Boolean(options?.isVoice),
    };
    if (!dispatchPendingSend(pending)) {
      pendingSendsRef.current.push(pending);
      setSocketReady(false);
    }
    return true;
  }

  async function toggleVoiceMode() {
    if (voiceEnabled) {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(voicePreferenceKey, "false");
      }
      await stopVoiceSession({ disableVoice: true, discardRecording: true });
      setVoiceError("");
      return;
    }
    let canUseVoice = backendVoiceEnabled;
    if (!canUseVoice) {
      try {
        canUseVoice = await refreshVoiceSettings();
      } catch {
        setVoiceError("Unable to fetch voice settings from backend. Restart the gateway and check port 8765.");
        return;
      }
    }
    if (!canUseVoice) {
      setVoiceError("Voice is disabled in backend settings. Enable [voice.enabled] and [voice.webchat_enabled] in config.toml.");
      return;
    }
    setVoiceEnabled(true);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(voicePreferenceKey, "true");
    }
    setVoiceError("");
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (sendMessage(input.trim())) {
      setInput("");
      setInputFromVoice(false);
      setVoiceError("");
    }
  }

  const voiceStatus = isSpeakingReply
    ? "Speaking"
    : isTranscribing
      ? "Transcribing"
      : isRecording
        ? "Listening"
        : isListening
          ? "Live"
          : voiceEnabled
            ? "Starting"
            : backendVoiceEnabled
              ? "Disabled"
              : "Unavailable";

  const voiceStatusMetaMap = {
    Disabled: { label: "Disabled", className: "border-slate-200 bg-slate-100 text-slate-600" },
    Live: { label: "Listening", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
    Listening: { label: "Recording", className: "border-blue-200 bg-blue-50 text-blue-700" },
    Transcribing: { label: "Transcribing…", className: "border-yellow-200 bg-yellow-50 text-yellow-700" },
    Speaking: { label: "Speaking", className: "border-purple-200 bg-purple-50 text-purple-700" },
    Starting: { label: "Starting…", className: "border-amber-200 bg-amber-50 text-amber-700" },
    Unavailable: { label: "Unavailable", className: "border-rose-200 bg-rose-50 text-rose-700" },
  } as const;
  const voiceStatusMeta = voiceStatusMetaMap[voiceStatus as keyof typeof voiceStatusMetaMap] ?? voiceStatusMetaMap.Unavailable;

  return (
    <section ref={rootSectionRef} className="rounded-[2rem] border border-white/85 bg-white/88 p-5 shadow-panel backdrop-blur sm:p-6">
      <div className="flex flex-col gap-4 border-b border-line/70 pb-5 xl:flex-row xl:items-end xl:justify-between">
        <div className="max-w-3xl">
          <p className="text-xs uppercase tracking-[0.28em] text-accent">Active Console</p>
          <h2 className="mt-2 font-display text-3xl leading-none text-ink sm:text-4xl">One thread, every capability.</h2>
          <p className="mt-3 text-sm leading-7 text-slate-600">
            Keep the main conversation clean here, then jump to Browser, Automation, or Host Access when you want a
            dedicated operational view.
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-4">
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
            <div className="mt-1">{voiceEnabled ? "Live voice + text" : "Slash + natural"}</div>
          </div>
          <button
            type="button"
            onClick={() => void toggleVoiceMode()}
            aria-label={voiceEnabled ? "Disable microphone" : "Enable microphone"}
            className={`rounded-[1.2rem] border px-4 py-3 text-left text-sm transition ${
              voiceEnabled
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : backendVoiceEnabled
                  ? "border-line/70 bg-white text-slate-700 hover:border-accent hover:text-accent"
                  : "border-line/70 bg-slate-100 text-slate-500"
            }`}
          >
            <div className="text-[11px] uppercase tracking-[0.2em]">Mic control</div>
            <div className="mt-1">{voiceEnabled ? "Disable mic" : backendVoiceEnabled ? "Enable mic" : "Mic unavailable"}</div>
          </button>
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
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div
            className={`flex items-center gap-3 rounded-[1.25rem] border px-4 py-3 text-sm transition ${
              voiceEnabled ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-line/70 bg-foam/70 text-slate-500"
            }`}
          >
            <div className="flex h-8 items-end gap-1" aria-hidden="false" aria-label="Voice level indicator">
              {waveformBars.map((height, index) => {
                const barClassName = isRecording
                  ? "bg-blue-500"
                  : isTranscribing
                    ? "animate-pulse bg-yellow-400"
                    : isSpeakingReply
                      ? "bg-purple-500"
                      : isListening
                        ? "bg-emerald-500"
                        : "bg-slate-300";
                return (
                  <span
                    key={index}
                    className={`w-1.5 rounded-full transition-all duration-150 ${barClassName}`}
                    style={{
                      height: `${voiceEnabled ? height : 4}px`,
                      opacity: voiceEnabled ? 0.8 : 0.45,
                    }}
                  />
                );
              })}
            </div>
            <div
              role="status"
              aria-live="polite"
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-medium uppercase tracking-[0.2em] ${voiceStatusMeta.className}`}
            >
              <span className="h-2 w-2 rounded-full bg-current" />
              <span>{voiceStatusMeta.label}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void toggleVoiceMode()}
            aria-label={voiceEnabled ? "Disable microphone" : "Enable microphone"}
            className={`rounded-[1.25rem] px-4 py-3 text-sm font-medium transition ${
              voiceEnabled
                ? "border border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100"
                : backendVoiceEnabled
                  ? "border border-line/70 bg-white text-slate-700 hover:border-accent hover:text-accent"
                  : "border border-line/70 bg-slate-100 text-slate-500"
            }`}
          >
            {voiceEnabled ? "Disable mic" : "Enable mic"}
          </button>
          {isRecording ? (
            <button
              type="button"
              onClick={() => stopSegmentRecording()}
              className="rounded-[1.25rem] border border-blue-200 bg-blue-50 px-4 py-3 text-sm font-medium text-blue-700 transition hover:bg-blue-100"
            >
              Stop & send
            </button>
          ) : null}
          {lastTranscript ? (
            <div className="min-w-[220px] flex-1 rounded-[1.25rem] border border-line/60 bg-white px-4 py-3 text-sm text-slate-600">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[11px] uppercase tracking-[0.2em] text-slate-400">Last heard</span>
                {lastTranscriptConfidence !== null && lastTranscriptConfidence < 0.6 ? (
                  <span className="text-xs text-slate-400">(low confidence)</span>
                ) : null}
              </div>
              <div className={showSentFlash ? "font-medium text-emerald-600" : "mt-1"}>
                {showSentFlash ? "✓ Sent" : lastTranscript}
              </div>
            </div>
          ) : null}
        </div>
        {isRecording && silenceProgress > 0 ? (
          <div className="mb-3 h-0.5 overflow-hidden rounded-full bg-rose-100">
            <div
              className="h-0.5 rounded-full bg-rose-400 transition-all"
              style={{ width: `${(1 - silenceProgress) * 100}%` }}
            />
          </div>
        ) : null}

        <form onSubmit={onSubmit} className="flex flex-col gap-3 lg:flex-row">
          <div className="min-w-0 flex-1">
            {inputFromVoice ? (
              <div className="mb-2 text-[11px] uppercase tracking-[0.2em] text-blue-500">
                Voice transcript — edit and press Send
              </div>
            ) : null}
            <input
              className={`min-w-0 w-full rounded-[1.25rem] border bg-foam/70 px-4 py-3 text-sm outline-none transition placeholder:text-slate-400 focus:border-accent ${
                inputFromVoice ? "border-blue-400" : "border-line/70"
              }`}
              value={input}
              onChange={(event) => {
                setInput(event.target.value);
                setInputFromVoice(false);
              }}
              placeholder={
                voiceEnabled
                  ? "Mic is live. Speak naturally or type here."
                  : "Ask about Gmail, GitHub, memory, browser actions, or try /skills"
              }
            />
          </div>
          <button
            className="rounded-[1.25rem] bg-accent px-6 py-3 text-sm font-medium text-white transition hover:bg-ink"
            type="submit"
          >
            Send
          </button>
        </form>
        {voiceError ? <p className="mt-3 text-sm text-rose-600">{voiceError}</p> : null}
        <p className="mt-3 text-xs uppercase tracking-[0.2em] text-slate-400">
          {voiceEnabled
            ? isSpeakingReply
              ? "Assistant is speaking. Voice capture resumes automatically when playback ends."
              : isTranscribing
                ? "Speech captured. Gemini is transcribing it now."
                : isRecording
                  ? "Listening for your voice. Your transcript will appear as a normal chat message."
                  : isListening
                    ? "Mic is live. Speak naturally and SonarBot will keep listening until you disable the mic. Hold Space to record."
                    : "Mic is starting up. Give it a moment to connect."
            : backendVoiceEnabled
              ? "Enable the mic to start continuous voice conversation with Gemini STT and TTS."
              : "Voice is disabled in backend config."}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {quickPrompts.map((prompt) => (
            <button
              key={prompt.label}
              type="button"
              onClick={() => {
                setInput(prompt.value);
                setInputFromVoice(false);
              }}
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
