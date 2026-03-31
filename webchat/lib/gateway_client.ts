export type GatewayFrame = Record<string, unknown>;

const GATEWAY_BASE_URL = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8765";

function gatewayPath(path: string): string {
  return typeof window === "undefined" ? `${GATEWAY_BASE_URL}${path}` : path;
}

export function createGatewaySocket(deviceId: string): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const host = process.env.NEXT_PUBLIC_GATEWAY_HOST || "localhost:8765";
  return new WebSocket(`${protocol}://${host}/webchat/ws?device_id=${deviceId}`);
}

export async function gatewayFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(gatewayPath(path), {
    cache: "no-store",
    ...init,
  });
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await gatewayFetch(path, init);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}`);
  }
  return response.json() as Promise<T>;
}
