"use client";

export type GatewayFrame = Record<string, unknown>;
export type VoiceTranscriptResult = {
  ok: boolean;
  text: string;
  confidence: number;
  duration_ms: number;
  input_mime: string;
  detected_language: string;
  metadata: Record<string, unknown>;
};

function gatewayHttpOrigin(): string {
  const host = window.location.hostname || "localhost";
  const protocol = window.location.protocol === "https:" ? "https" : "http";
  return `${protocol}://${host}:8765`;
}

export function createGatewaySocket(deviceId: string): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.hostname || "localhost";
  return new WebSocket(`${protocol}://${host}:8765/webchat/ws?device_id=${encodeURIComponent(deviceId)}`);
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${gatewayHttpOrigin()}${path}`, { cache: "no-store", credentials: "include" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}`);
  }
  return response.json() as Promise<T>;
}

export async function transcribeVoiceClip(
  blob: Blob,
  durationMs: number,
  deviceId?: string,
): Promise<VoiceTranscriptResult> {
  const query = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : "";
  const contentType = (blob.type || "audio/webm").split(";")[0]?.trim() || "audio/webm";
  const response = await fetch(`${gatewayHttpOrigin()}/webchat/voice/transcribe${query}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": contentType,
      "X-Audio-Duration-Ms": String(durationMs),
    },
    body: blob,
  });
  const payload = (await response.json()) as VoiceTranscriptResult | { detail?: string };
  if (!response.ok) {
    throw new Error(typeof (payload as { detail?: string }).detail === "string" ? (payload as { detail?: string }).detail! : "Voice transcription failed.");
  }
  return payload as VoiceTranscriptResult;
}

export async function synthesizeVoiceReply(text: string): Promise<Blob> {
  const response = await fetch(`${gatewayHttpOrigin()}/webchat/voice/synthesize`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    let detail = "Voice synthesis failed.";
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        detail = payload.detail;
      }
    } catch {
      // ignore parse errors and keep the generic message
    }
    throw new Error(detail);
  }
  return await response.blob();
}
