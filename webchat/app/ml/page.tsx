import { WorkspaceHero } from "../../components/WorkspaceHero";

async function getMlStatus() {
  const endpoint = "http://localhost:3000/api/ml/status";
  try {
    const response = await fetch(endpoint, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      return payload ?? { status: "gateway_unavailable", endpoint };
    }
    return payload;
  } catch (error) {
    return {
      status: "gateway_unavailable",
      endpoint,
      error: error instanceof Error ? error.message : "Unknown fetch failure",
    };
  }
}

export default async function MLPage() {
  const status = await getMlStatus();
  const toolRouter = status?.models?.tool_router ?? status?.tool_router ?? {};
  const memoryClassifier = status?.models?.memory_classifier ?? status?.memory_classifier ?? {};
  const browserIntent = status?.models?.browser_intent_onnx ?? status?.browser_intent_onnx ?? {};
  const metrics = status?.metrics?.tool_router ?? {};
  const globalCards = [
    { label: "ML Enabled", value: status?.enabled ? "Yes" : "No" },
    { label: "Timestamp", value: status?.timestamp ?? "-" },
  ];
  const toolRouterCards = [
    { label: "Enabled", value: toolRouter?.enabled ? "Yes" : "No" },
    { label: "Shadow Mode", value: toolRouter?.shadow_mode ? "Yes" : "No" },
    { label: "Model Loaded", value: toolRouter?.model_loaded ? "Yes" : "No" },
    { label: "Avg Latency (ms)", value: Number(metrics?.avg_latency_ms ?? 0).toFixed(2) },
    { label: "Avg Confidence", value: Number(metrics?.avg_confidence ?? 0).toFixed(3) },
    { label: "Fallback Rate", value: `${(Number(metrics?.fallback_rate ?? 0) * 100).toFixed(1)}%` },
    { label: "Tools Saved", value: metrics?.tools_saved_total ?? 0 },
    { label: "Requests", value: metrics?.requests ?? 0 },
  ];
  const memoryCards = [
    { label: "Enabled", value: memoryClassifier?.enabled ? "Yes" : "No" },
    { label: "Model Loaded", value: memoryClassifier?.model_loaded ? "Yes" : "No" },
    { label: "Min Confidence", value: Number(memoryClassifier?.min_confidence ?? 0).toFixed(2) },
    { label: "Model Error", value: memoryClassifier?.model_error || "-" },
  ];
  const browserIntentCards = [
    { label: "Enabled", value: browserIntent?.enabled ? "Yes" : "No" },
    { label: "Model Loaded", value: browserIntent?.model_loaded ? "Yes" : "No" },
    { label: "ONNX Runtime", value: browserIntent?.onnxruntime_available ? "Available" : "Missing" },
    { label: "Model Error", value: browserIntent?.model_error || "-" },
  ];

  return (
    <main className="space-y-6">
      <WorkspaceHero
        eyebrow="Machine Learning"
        title="Live ML routing status and runtime health."
        description="This panel reads SonarBot backend ML telemetry from /api/ml/status and shows current tool-router behavior."
        badges={[
          { label: "Endpoint", value: "/api/ml/status" },
          { label: "Scope", value: "Tool routing + metrics" },
          { label: "Mode", value: "No-store live fetch" },
        ]}
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {globalCards.map((card) => (
          <section key={card.label} className="rounded-[1.75rem] border border-white/85 bg-white/90 p-5 shadow-card">
            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{card.label}</p>
            <p className="mt-3 text-2xl font-semibold">{card.value}</p>
          </section>
        ))}
      </div>

      <section className="space-y-3 rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel">
        <h2 className="text-sm uppercase tracking-[0.16em] text-slate-500">Tool Router Analytics</h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {toolRouterCards.map((card) => (
            <div key={card.label} className="rounded-[1.35rem] border border-white/80 bg-white/80 p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500">{card.label}</p>
              <p className="mt-2 text-xl font-semibold">{card.value}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-3 rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel">
        <h2 className="text-sm uppercase tracking-[0.16em] text-slate-500">Memory Classifier Analytics</h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {memoryCards.map((card) => (
            <div key={card.label} className="rounded-[1.35rem] border border-white/80 bg-white/80 p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500">{card.label}</p>
              <p className="mt-2 text-xl font-semibold">{card.value}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-3 rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel">
        <h2 className="text-sm uppercase tracking-[0.16em] text-slate-500">Browser Intent ONNX Analytics</h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {browserIntentCards.map((card) => (
            <div key={card.label} className="rounded-[1.35rem] border border-white/80 bg-white/80 p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500">{card.label}</p>
              <p className="mt-2 text-xl font-semibold">{card.value}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel">
        <h2 className="text-sm uppercase tracking-[0.16em] text-slate-500">Raw Payload</h2>
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-sm leading-6 text-slate-700">
          {JSON.stringify(status ?? { status: "Gateway unavailable", endpoint: "/api/ml/status" }, null, 2)}
        </pre>
      </section>
    </main>
  );
}
