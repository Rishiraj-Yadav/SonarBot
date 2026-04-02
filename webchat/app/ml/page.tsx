import { WorkspaceHero } from "../../components/WorkspaceHero";

function clampPercent(value: unknown) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.max(0, Math.min(100, numeric));
}

function formatBytes(value: unknown) {
  const bytes = Number(value ?? 0);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatTimestamp(value: unknown) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "-";
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString();
}

function ProgressStat({
  label,
  value,
  hint,
  accent = "from-sky-500 to-blue-600",
}: {
  label: string;
  value: number;
  hint: string;
  accent?: string;
}) {
  const width = clampPercent(value);
  return (
    <div className="rounded-[1.5rem] border border-white/80 bg-white/80 p-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.14em] text-slate-500">{label}</p>
          <p className="mt-2 text-2xl font-semibold">{width.toFixed(1)}%</p>
        </div>
        <p className="text-right text-xs text-slate-500">{hint}</p>
      </div>
      <div className="mt-4 h-3 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full rounded-full bg-gradient-to-r ${accent}`} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

function ComparisonBars({
  title,
  leftLabel,
  leftValue,
  rightLabel,
  rightValue,
  accent = "bg-blue-600",
}: {
  title: string;
  leftLabel: string;
  leftValue: number;
  rightLabel: string;
  rightValue: number;
  accent?: string;
}) {
  const total = Math.max(1, leftValue + rightValue);
  const leftPercent = (leftValue / total) * 100;
  const rightPercent = (rightValue / total) * 100;
  return (
    <div className="rounded-[1.5rem] border border-white/80 bg-white/80 p-4">
      <p className="text-xs uppercase tracking-[0.14em] text-slate-500">{title}</p>
      <div className="mt-4 space-y-3">
        <div>
          <div className="mb-1 flex items-center justify-between text-sm text-slate-700">
            <span>{leftLabel}</span>
            <span>{leftValue}</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-slate-200">
            <div className={`h-full rounded-full ${accent}`} style={{ width: `${leftPercent}%` }} />
          </div>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between text-sm text-slate-700">
            <span>{rightLabel}</span>
            <span>{rightValue}</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-slate-200">
            <div className="h-full rounded-full bg-slate-500" style={{ width: `${rightPercent}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

function VerticalBars({
  title,
  bars,
}: {
  title: string;
  bars: Array<{ label: string; value: number; tone: string }>;
}) {
  const maxValue = Math.max(1, ...bars.map((bar) => bar.value));
  return (
    <div className="rounded-[1.5rem] border border-white/80 bg-white/80 p-4">
      <p className="text-xs uppercase tracking-[0.14em] text-slate-500">{title}</p>
      <div className="mt-6 flex h-40 items-end gap-4">
        {bars.map((bar) => {
          const height = Math.max(10, (bar.value / maxValue) * 100);
          return (
            <div key={bar.label} className="flex flex-1 flex-col items-center gap-2">
              <span className="text-xs font-medium text-slate-600">{bar.value}</span>
              <div className="flex h-28 w-full items-end rounded-[1rem] bg-slate-100 p-2">
                <div className={`w-full rounded-[0.85rem] ${bar.tone}`} style={{ height: `${height}%` }} />
              </div>
              <span className="text-center text-xs text-slate-500">{bar.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

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
  const browserIntentLocal = status?.models?.browser_intent_local ?? status?.browser_intent_local ?? {};
  const browserIntent = status?.models?.browser_intent_onnx ?? status?.browser_intent_onnx ?? {};
  const metrics = status?.metrics?.tool_router ?? {};
  const memoryMetrics = status?.metrics?.memory_classifier ?? {};
  const toolConfidencePercent = Number(metrics?.avg_confidence ?? 0) * 100;
  const toolFallbackPercent = Number(metrics?.fallback_rate ?? 0) * 100;
  const memoryKeepPercent = Number(memoryMetrics?.keep_rate ?? 0) * 100;
  const memoryConfidencePercent = Number(memoryMetrics?.avg_confidence ?? 0) * 100;
  const browserReadinessPercent = browserIntent?.model_loaded ? 100 : browserIntentLocal?.model_loaded ? 55 : 15;
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
    { label: "Tool Labels", value: toolRouter?.label_count ?? 0 },
    { label: "Safety Tools", value: toolRouter?.safety_tool_count ?? 0 },
    { label: "Tools Available", value: metrics?.tools_available_total ?? 0 },
    { label: "Tools Selected", value: metrics?.tools_selected_total ?? 0 },
    { label: "Feature Count", value: toolRouter?.feature_count ?? 0 },
    { label: "Artifact Size", value: formatBytes(toolRouter?.model_bytes) },
    { label: "Model Type", value: toolRouter?.model_type || "-" },
    { label: "Updated", value: formatTimestamp(toolRouter?.model_updated_at) },
  ];
  const memoryCards = [
    { label: "Enabled", value: memoryClassifier?.enabled ? "Yes" : "No" },
    { label: "Model Loaded", value: memoryClassifier?.model_loaded ? "Yes" : "No" },
    { label: "Min Confidence", value: Number(memoryClassifier?.min_confidence ?? 0).toFixed(2) },
    { label: "Model Error", value: memoryClassifier?.model_error || "-" },
    { label: "Decisions", value: memoryMetrics?.decisions ?? 0 },
    { label: "Kept", value: memoryMetrics?.kept ?? 0 },
    { label: "Dropped", value: memoryMetrics?.dropped ?? 0 },
    { label: "Keep Rate", value: `${(Number(memoryMetrics?.keep_rate ?? 0) * 100).toFixed(1)}%` },
    { label: "Avg Confidence", value: Number(memoryMetrics?.avg_confidence ?? 0).toFixed(3) },
    { label: "Decision Reason", value: memoryMetrics?.last_reason || "-" },
    { label: "Feature Count", value: memoryClassifier?.feature_count ?? 0 },
    { label: "Artifact Size", value: formatBytes(memoryClassifier?.model_bytes) },
    { label: "Model Type", value: memoryClassifier?.model_type || "-" },
    { label: "Updated", value: formatTimestamp(memoryClassifier?.model_updated_at) },
  ];
  const browserIntentCards = [
    { label: "Local Starter", value: browserIntentLocal?.model_loaded ? "Loaded" : "Missing" },
    { label: "Local Labels", value: browserIntentLocal?.label_count ?? (Array.isArray(browserIntentLocal?.labels) ? browserIntentLocal.labels.length : 0) },
    { label: "Enabled", value: browserIntent?.enabled ? "Yes" : "No" },
    { label: "Model Loaded", value: browserIntent?.model_loaded ? "Yes" : "No" },
    { label: "ONNX Runtime", value: browserIntent?.onnxruntime_available ? "Available" : "Missing" },
    { label: "Model Error", value: browserIntent?.model_error || "-" },
    { label: "Feature Count", value: browserIntentLocal?.feature_count ?? 0 },
    { label: "Local Artifact", value: formatBytes(browserIntentLocal?.model_bytes) },
    { label: "Local Model Type", value: browserIntentLocal?.model_type || "-" },
    { label: "Local Updated", value: formatTimestamp(browserIntentLocal?.model_updated_at) },
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
        <div className="grid gap-4 lg:grid-cols-3">
          <ProgressStat
            label="Confidence"
            value={toolConfidencePercent}
            hint={`${metrics?.requests ?? 0} routed requests`}
          />
          <ProgressStat
            label="Fallback Rate"
            value={toolFallbackPercent}
            hint={`${metrics?.fallbacks ?? 0} fallback turns`}
            accent="from-amber-400 to-orange-500"
          />
          <ComparisonBars
            title="Tool Volume"
            leftLabel="Saved"
            leftValue={Number(metrics?.tools_saved_total ?? 0)}
            rightLabel="Selected"
            rightValue={Number(metrics?.tools_selected_total ?? 0)}
            accent="bg-emerald-500"
          />
        </div>
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
        <div className="grid gap-4 lg:grid-cols-3">
          <ProgressStat
            label="Keep Rate"
            value={memoryKeepPercent}
            hint={`${memoryMetrics?.kept ?? 0} kept memories`}
            accent="from-emerald-400 to-teal-500"
          />
          <ProgressStat
            label="Avg Confidence"
            value={memoryConfidencePercent}
            hint={`${memoryMetrics?.decisions ?? 0} model decisions`}
            accent="from-cyan-500 to-sky-600"
          />
          <ComparisonBars
            title="Decision Mix"
            leftLabel="Kept"
            leftValue={Number(memoryMetrics?.kept ?? 0)}
            rightLabel="Dropped"
            rightValue={Number(memoryMetrics?.dropped ?? 0)}
            accent="bg-emerald-500"
          />
        </div>
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
        <div className="grid gap-4 lg:grid-cols-3">
          <ProgressStat
            label="ONNX Readiness"
            value={browserReadinessPercent}
            hint={browserIntent?.model_loaded ? "Fully exported" : browserIntentLocal?.model_loaded ? "Starter model ready" : "Training pending"}
            accent="from-violet-500 to-indigo-600"
          />
          <ComparisonBars
            title="Artifact Footprint"
            leftLabel="Local Starter"
            leftValue={Number(browserIntentLocal?.model_bytes ?? 0)}
            rightLabel="ONNX"
            rightValue={Number(browserIntent?.model_loaded ? browserIntent?.model_bytes ?? 0 : 0)}
            accent="bg-indigo-500"
          />
          <VerticalBars
            title="Model Complexity"
            bars={[
              { label: "Labels", value: Number(browserIntentLocal?.label_count ?? 0), tone: "bg-indigo-500" },
              { label: "Features", value: Number(browserIntentLocal?.feature_count ?? 0), tone: "bg-sky-500" },
              { label: "Runtime", value: browserIntent?.onnxruntime_available ? 1 : 0, tone: "bg-emerald-500" },
            ]}
          />
        </div>
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
