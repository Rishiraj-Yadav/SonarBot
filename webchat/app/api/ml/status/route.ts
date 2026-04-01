import { NextResponse } from "next/server";
import { gatewayUrl } from "../../../../lib/backend";

export async function GET() {
  const target = gatewayUrl("/api/ml/status");
  try {
    const response = await fetch(target, { cache: "no-store" });
    const text = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        {
          status: "gateway_error",
          endpoint: target,
          status_code: response.status,
          body: text,
        },
        { status: 502 },
      );
    }
    try {
      return NextResponse.json(JSON.parse(text), { status: 200 });
    } catch {
      return NextResponse.json(
        {
          status: "invalid_json",
          endpoint: target,
          body: text,
        },
        { status: 502 },
      );
    }
  } catch (error) {
    return NextResponse.json(
      {
        status: "gateway_unreachable",
        endpoint: target,
        error: error instanceof Error ? error.message : "Unknown fetch failure",
      },
      { status: 502 },
    );
  }
}

