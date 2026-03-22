"use client";

export type GatewayFrame = Record<string, unknown>;

export function createGatewaySocket(deviceId: string): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${protocol}://localhost:8765/webchat/ws?device_id=${deviceId}`);
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`http://localhost:8765${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}`);
  }
  return response.json() as Promise<T>;
}
