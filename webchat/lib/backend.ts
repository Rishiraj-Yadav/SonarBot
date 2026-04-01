const DEFAULT_GATEWAY_URL = "http://localhost:8765";

export function gatewayBaseUrl(): string {
  const envValue = process.env.NEXT_PUBLIC_GATEWAY_URL?.trim();
  if (!envValue) {
    return DEFAULT_GATEWAY_URL;
  }
  return envValue.replace(/\/+$/, "");
}

export function gatewayUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${gatewayBaseUrl()}${normalizedPath}`;
}

