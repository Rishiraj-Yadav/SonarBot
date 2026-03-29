"use client";

import { type MouseEvent, useEffect, useRef, useState } from "react";

import { fetchJson } from "../lib/gateway_client";

type BrowserTab = {
  tab_id: string;
  title: string;
  url: string;
  active: boolean;
  mode?: string;
};

type BrowserLog = {
  timestamp: string;
  kind: string;
  level: string;
  message: string;
  tab_id: string;
  url: string;
};

type BrowserDownload = {
  path: string;
  filename: string;
  created_at: string;
  size: number;
};

type BrowserProfile = {
  profile_key: string;
  site_name: string;
  profile_name: string;
  status: string;
  last_used_at: string;
};

type BrowserState = {
  active: boolean;
  headless: boolean;
  current_mode?: string;
  current_tab_id: string | null;
  active_tab?: BrowserTab | null;
  tabs: BrowserTab[];
  active_profile?: BrowserProfile | null;
  streaming: boolean;
  pending_protected_action?: {
    action_type?: string;
    selector?: string;
    target?: string;
  } | null;
  active_workflow?: BrowserActiveWorkflow | null;
  workflow_stop_requested?: boolean;
  pending_vision_click?: BrowserVisionTarget | null;
};

type BrowserWorkflow = {
  recipe_name?: string;
  site_name?: string;
  status?: string;
  response_text?: string;
  message?: string;
  step_name?: string;
  reason?: string;
  query?: string;
  target_url?: string;
  current_url?: string;
  last_step?: {
    step_name?: string;
    message?: string;
    status?: string;
  } | null;
};

type BrowserActiveWorkflow = {
  recipe_name?: string;
  site_name?: string;
  query?: string;
  action?: string;
  execution_mode?: string;
  status?: string;
  response_text?: string;
  last_step?: {
    step_name?: string;
    message?: string;
    status?: string;
  } | null;
  started_at?: string;
  target_url?: string;
  current_url?: string;
};

type BrowserScreenshot = {
  image_data_url?: string;
  tab_id?: string;
  url?: string;
  title?: string;
  mode?: string;
};

type BrowserVisionTarget = {
  x?: number;
  y?: number;
  confidence?: number;
  label?: string;
  reason?: string;
  screenshot_path?: string;
  image_data_url?: string;
  image_width?: number;
  image_height?: number;
  requires_confirmation?: boolean;
  clicked?: boolean;
  bbox?: {
    left?: number;
    top?: number;
    width?: number;
    height?: number;
  } | null;
};

function shortTime(value: string) {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) {
    return value;
  }
  return parsed.toLocaleString();
}

function prettifySiteName(value?: string | null) {
  const raw = (value || "").trim();
  if (!raw) {
    return "browser";
  }
  return raw
    .replace(/^https?:\/\//i, "")
    .replace(/^www\./i, "")
    .replace(/\.(com|in|co\.in|org|net)$/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function formatWorkflowStatus(workflow: BrowserWorkflow | BrowserActiveWorkflow | null | undefined) {
  if (!workflow) {
    return "Working...";
  }
  const siteLabel = prettifySiteName(workflow.site_name || workflow.recipe_name || "browser");
  const stepName = (workflow.last_step?.step_name || "").trim().toLowerCase();
  const stepMessage = (workflow.last_step?.message || "").trim();
  const status = (workflow.status || "").trim().toLowerCase();
  if (stepMessage) {
    return stepMessage;
  }
  if (status === "blocked") {
    return "Paused for review";
  }
  if (status === "completed") {
    return "Completed";
  }
  if (stepName === "open_site" || stepName === "open_store" || stepName === "open_food_site" || stepName === "open_travel_site") {
    return `Opening ${siteLabel}`;
  }
  if (stepName === "type_query" || stepName === "fill_fields") {
    return `Typing query for ${siteLabel}`;
  }
  if (stepName === "submit_query" || stepName === "search" || stepName === "search_food") {
    return `Searching ${siteLabel}`;
  }
  if (stepName === "open_result" || stepName === "open_problem") {
    return `Opening result on ${siteLabel}`;
  }
  return `Working on ${siteLabel}`;
}

function formatWorkflowDetail(workflow: BrowserWorkflow | BrowserActiveWorkflow | null | undefined) {
  if (!workflow) {
    return "";
  }
  const query = (workflow.query || "").trim();
  const url = (workflow.current_url || workflow.target_url || "").trim();
  if (query) {
    return query;
  }
  return url;
}

export function BrowserPanel() {
  const [state, setState] = useState<BrowserState | null>(null);
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [logs, setLogs] = useState<BrowserLog[]>([]);
  const [downloads, setDownloads] = useState<BrowserDownload[]>([]);
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [screenshot, setScreenshot] = useState("");
  const [workflow, setWorkflow] = useState<BrowserWorkflow | null>(null);
  const [workflowEvents, setWorkflowEvents] = useState<BrowserWorkflow[]>([]);
  const [visionTarget, setVisionTarget] = useState<BrowserVisionTarget | null>(null);
  const [imageMetrics, setImageMetrics] = useState({
    naturalWidth: 0,
    naturalHeight: 0,
    displayWidth: 0,
    displayHeight: 0,
    offsetX: 0,
    offsetY: 0,
  });
  const screenshotRef = useRef<HTMLImageElement | null>(null);
  const loadInFlightRef = useRef(false);
  const loadAbortRef = useRef<AbortController | null>(null);

  const fetchBrowserData = async (signal?: AbortSignal) => {
    const [stateData, tabsData, logsData, downloadsData, profilesData, screenshotData] = await Promise.all([
      fetchJson<{ state: BrowserState }>("/api/browser/state", { signal }),
      fetchJson<{ tabs: BrowserTab[] }>("/api/browser/tabs", { signal }),
      fetchJson<{ logs: BrowserLog[] }>("/api/browser/logs?limit=8", { signal }),
      fetchJson<{ downloads: BrowserDownload[] }>("/api/browser/downloads?limit=8", { signal }),
      fetchJson<{ profiles: BrowserProfile[] }>("/api/browser/profiles", { signal }),
      fetchJson<{ screenshot: BrowserScreenshot | null }>("/api/browser/live-screenshot", { signal }),
    ]);
    return { stateData, tabsData, logsData, downloadsData, profilesData, screenshotData };
  };

  const applyBrowserData = (data: Awaited<ReturnType<typeof fetchBrowserData>>) => {
    const { stateData, tabsData, logsData, downloadsData, profilesData, screenshotData } = data;
    setState(stateData.state ?? null);
    setTabs(tabsData.tabs ?? []);
    setLogs(logsData.logs ?? []);
    setDownloads(downloadsData.downloads ?? []);
    setProfiles(profilesData.profiles ?? []);
    setWorkflow(stateData.state?.active_workflow ? { ...stateData.state.active_workflow } : null);
    setVisionTarget((stateData.state?.pending_vision_click as BrowserVisionTarget | null) ?? null);
    if (screenshotData.screenshot?.image_data_url) {
      setScreenshot(screenshotData.screenshot.image_data_url);
    }
  };

  const computeImageMetrics = () => {
    const element = screenshotRef.current;
    if (!element) {
      return null;
    }
    const bounds = element.getBoundingClientRect();
    if (!bounds.width || !bounds.height || !element.naturalWidth || !element.naturalHeight) {
      return null;
    }
    const scale = Math.min(bounds.width / element.naturalWidth, bounds.height / element.naturalHeight);
    const displayWidth = element.naturalWidth * scale;
    const displayHeight = element.naturalHeight * scale;
    return {
      naturalWidth: element.naturalWidth,
      naturalHeight: element.naturalHeight,
      displayWidth,
      displayHeight,
      offsetX: (bounds.width - displayWidth) / 2,
      offsetY: (bounds.height - displayHeight) / 2,
    };
  };

  const syncImageMetrics = () => {
    const metrics = computeImageMetrics();
    if (metrics) {
      setImageMetrics(metrics);
    }
  };

  const handleScreenshotClick = async (event: MouseEvent<HTMLImageElement>) => {
    const element = screenshotRef.current;
    if (!element || !state?.current_tab_id) {
      return;
    }
    const bounds = element.getBoundingClientRect();
    const metrics = computeImageMetrics();
    if (!bounds.width || !bounds.height || !metrics) {
      return;
    }
    const localX = event.clientX - bounds.left - metrics.offsetX;
    const localY = event.clientY - bounds.top - metrics.offsetY;
    if (
      localX < 0 ||
      localY < 0 ||
      localX > metrics.displayWidth ||
      localY > metrics.displayHeight
    ) {
      return;
    }
    const x = Math.round((localX / metrics.displayWidth) * metrics.naturalWidth);
    const y = Math.round((localY / metrics.displayHeight) * metrics.naturalHeight);
    try {
      const response = await fetch("http://localhost:8765/webchat/browser/click", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ x, y, tab_id: state.current_tab_id }),
      });
      if (!response.ok) {
        return;
      }
      applyBrowserData(await fetchBrowserData());
    } catch {
      return;
    }
  };

  const stopActiveWorkflow = async () => {
    try {
      await fetch("http://localhost:8765/api/browser/workflow/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({}),
      });
    } catch {
      return;
    }
    await applyBrowserData(await fetchBrowserData());
  };

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      if (loadInFlightRef.current) {
        return;
      }
      const controller = new AbortController();
      loadAbortRef.current?.abort();
      loadAbortRef.current = controller;
      loadInFlightRef.current = true;
      try {
        const data = await fetchBrowserData(controller.signal);
        if (!mounted) {
          return;
        }
        applyBrowserData(data);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        return;
      } finally {
        if (loadAbortRef.current === controller) {
          loadAbortRef.current = null;
        }
        loadInFlightRef.current = false;
      }
    };

      const onBrowserState = (event: Event) => {
      const detail = (event as CustomEvent<BrowserState>).detail;
      if (detail) {
        setState(detail);
        setTabs(detail.tabs ?? []);
        setVisionTarget((detail.pending_vision_click as BrowserVisionTarget | null) ?? null);
      }
      void load();
    };

    const onBrowserLog = (event: Event) => {
      const detail = (event as CustomEvent<BrowserLog>).detail;
      if (!detail) {
        return;
      }
      setLogs((current) => [detail, ...current].slice(0, 8));
    };

    const onBrowserDownload = (event: Event) => {
      const detail = (event as CustomEvent<BrowserDownload>).detail;
      if (!detail) {
        return;
      }
      setDownloads((current) => [detail, ...current].slice(0, 8));
    };

    const onBrowserScreenshot = (event: Event) => {
      const detail = (event as CustomEvent<{ image_data_url?: string }>).detail;
      if (detail?.image_data_url) {
        setScreenshot(detail.image_data_url);
      }
    };

    const onVisionTarget = (event: Event) => {
      const detail = (event as CustomEvent<BrowserVisionTarget>).detail;
      if (detail) {
        setVisionTarget(detail);
        if (detail.image_data_url) {
          setScreenshot(detail.image_data_url);
        }
      }
    };

    const onSessionExpired = () => {
      void load();
    };

    const appendWorkflowEvent = (kind: string, event: Event) => {
      const detail = (event as CustomEvent<BrowserWorkflow>).detail;
      if (!detail) {
        return;
      }
      const nextEntry = { ...detail, reason: kind };
      setWorkflow(nextEntry);
      setWorkflowEvents((current) => [nextEntry, ...current].slice(0, 10));
    };
    const onWorkflowStarted = (event: Event) => appendWorkflowEvent("started", event);
    const onWorkflowStep = (event: Event) => appendWorkflowEvent("step", event);
    const onWorkflowBlocked = (event: Event) => appendWorkflowEvent("blocked", event);
    const onWorkflowCompleted = (event: Event) => appendWorkflowEvent("completed", event);

    void load();
    window.addEventListener("resize", syncImageMetrics);
    window.addEventListener("sonarbot:browser.state", onBrowserState);
    window.addEventListener("sonarbot:browser.log", onBrowserLog);
    window.addEventListener("sonarbot:browser.download", onBrowserDownload);
    window.addEventListener("sonarbot:browser.screenshot", onBrowserScreenshot);
    window.addEventListener("sonarbot:browser.vision_target", onVisionTarget);
    window.addEventListener("sonarbot:browser.session_expired", onSessionExpired);
    window.addEventListener("sonarbot:browser.workflow.started", onWorkflowStarted);
    window.addEventListener("sonarbot:browser.workflow.step", onWorkflowStep);
    window.addEventListener("sonarbot:browser.workflow.blocked", onWorkflowBlocked);
    window.addEventListener("sonarbot:browser.workflow.completed", onWorkflowCompleted);
    const timer = window.setInterval(() => void load(), 12000);

    return () => {
      mounted = false;
      loadAbortRef.current?.abort();
      window.clearInterval(timer);
      window.removeEventListener("resize", syncImageMetrics);
      window.removeEventListener("sonarbot:browser.state", onBrowserState);
      window.removeEventListener("sonarbot:browser.log", onBrowserLog);
      window.removeEventListener("sonarbot:browser.download", onBrowserDownload);
      window.removeEventListener("sonarbot:browser.screenshot", onBrowserScreenshot);
      window.removeEventListener("sonarbot:browser.vision_target", onVisionTarget);
      window.removeEventListener("sonarbot:browser.session_expired", onSessionExpired);
      window.removeEventListener("sonarbot:browser.workflow.started", onWorkflowStarted);
      window.removeEventListener("sonarbot:browser.workflow.step", onWorkflowStep);
      window.removeEventListener("sonarbot:browser.workflow.blocked", onWorkflowBlocked);
      window.removeEventListener("sonarbot:browser.workflow.completed", onWorkflowCompleted);
    };
  }, []);

  return (
    <div className="space-y-4">
      <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel backdrop-blur">
        <div className="border-b border-line/70 pb-4">
          <p className="text-xs uppercase tracking-[0.24em] text-accent">Browser Workspace</p>
          <h2 className="mt-2 font-display text-3xl text-ink">Live browser state</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Active profile, tabs, downloads, logs, and headed-browser screenshots flow here from the Playwright runtime.
          </p>
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,0.86fr)_minmax(320px,1.14fr)]">
          <div className="space-y-3">
            <div className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-medium text-ink">{state?.active ? "Browser active" : "Browser idle"}</div>
                <div className="rounded-full bg-sand px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-slate-600">
                  {state?.current_mode || (state?.headless ? "headless" : "headed")}
                </div>
              </div>
              <div className="mt-2 text-sm text-slate-600">
                {state?.active_profile
                  ? `${state.active_profile.site_name} / ${state.active_profile.profile_name} (${state.active_profile.status})`
                  : "No active profile"}
              </div>
              <div className="mt-2 text-xs uppercase tracking-[0.18em] text-slate-500">
                {state?.streaming ? "Live screenshot stream on" : "Live screenshot stream off"}
              </div>
              {state?.pending_protected_action ? (
                <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Waiting for review: {state.pending_protected_action.action_type || "browser action"} on{" "}
                  {state.pending_protected_action.selector || state.pending_protected_action.target || "the current page"}.
                  Reply with confirm or cancel.
                </div>
              ) : null}
            </div>
            <div className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4 text-sm leading-6 text-slate-600">
              Profiles stay named per site/account, downloads land in the workspace inbox, and stale sessions remain
              inspectable instead of being silently deleted.
            </div>
            <div className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Workflow</div>
                {state?.active_workflow ? (
                  <button
                    type="button"
                    onClick={() => void stopActiveWorkflow()}
                    className="rounded-full bg-rose-100 px-3 py-2 text-[10px] uppercase tracking-[0.18em] text-rose-700 transition hover:bg-rose-200"
                  >
                    Terminate task
                  </button>
                ) : null}
              </div>
              {state?.active_workflow ? (
                <div className="mt-2 space-y-2 rounded-2xl border border-line/70 bg-foam/70 p-3">
                  <div className="text-sm font-medium text-ink">{formatWorkflowStatus(state.active_workflow)}</div>
                  <div className="text-sm text-slate-600">
                    {formatWorkflowDetail(state.active_workflow) || "No query yet"}
                  </div>
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">
                    {state.active_workflow.status || "running"}{state.workflow_stop_requested ? " | stop requested" : ""}
                  </div>
                  {(() => {
                    const pageUrl = state.active_tab?.url || state.active_workflow?.current_url || state.active_workflow?.target_url || "";
                    return pageUrl ? (
                      <a
                        href={pageUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="block rounded-xl border border-sky-200 bg-white/80 px-3 py-2 text-sm text-sky-800 transition hover:bg-sky-50"
                      >
                        <div className="text-[11px] uppercase tracking-[0.18em] text-sky-500">Current page</div>
                        <div className="mt-1 break-all">{pageUrl}</div>
                      </a>
                    ) : null;
                  })()}
                  {state.active_workflow.last_step ? (
                    <div className="rounded-xl bg-white/80 px-3 py-2 text-sm text-slate-600">
                      <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Last step</div>
                      <div className="mt-1">{state.active_workflow.last_step.message || state.active_workflow.last_step.step_name || "Running"}</div>
                    </div>
                  ) : null}
                </div>
              ) : workflow ? (
                <>
                  <div className="mt-2 text-sm font-medium text-ink">{formatWorkflowStatus(workflow)}</div>
                  <div className="mt-1 text-sm text-slate-600">{formatWorkflowDetail(workflow) || workflow.response_text || workflow.message || "Waiting for workflow updates."}</div>
                  <div className="mt-2 text-xs uppercase tracking-[0.18em] text-slate-500">
                    {workflow.status || "running"}
                  </div>
                </>
              ) : (
                <div className="mt-2 text-sm text-slate-500">No browser workflow has run yet.</div>
              )}
              {workflowEvents.length > 0 ? (
                <div className="mt-4 space-y-2">
                  <div className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-sky-500">Live step</div>
                    <div className="mt-1">{formatWorkflowStatus(workflowEvents[0])}</div>
                    <div className="mt-1 text-xs text-sky-700">
                      {formatWorkflowDetail(workflowEvents[0]) || workflowEvents[0]?.message || workflowEvents[0]?.response_text || ""}
                    </div>
                  </div>
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Recent steps</div>
                  {workflowEvents.map((entry, index) => (
                    <div key={`${entry.reason || "workflow"}-${index}-${entry.step_name || "step"}`} className="rounded-xl border border-line/60 bg-white/85 px-3 py-2 text-sm text-slate-600">
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-medium text-ink">{entry.step_name || entry.recipe_name || "workflow"}</div>
                        <div className="text-[10px] uppercase tracking-[0.18em] text-slate-400">{entry.reason || entry.status || "update"}</div>
                      </div>
                      <div className="mt-1">{entry.message || entry.response_text || "Workflow update"}</div>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
          <div className="overflow-hidden rounded-[1.35rem] border border-line/80 bg-slate-950">
            {screenshot ? (
              <div className="relative flex h-64 items-center justify-center overflow-hidden">
                <img
                  ref={screenshotRef}
                  src={screenshot}
                  alt="Live browser"
                  className="h-64 w-full cursor-crosshair object-contain"
                  onClick={handleScreenshotClick}
                  onLoad={syncImageMetrics}
                />
                {visionTarget && imageMetrics.displayWidth > 0 ? (
                  <div
                    className="pointer-events-none absolute"
                    style={{
                      left: `${imageMetrics.offsetX}px`,
                      top: `${imageMetrics.offsetY}px`,
                      width: `${imageMetrics.displayWidth}px`,
                      height: `${imageMetrics.displayHeight}px`,
                    }}
                  >
                    <div
                      className="absolute rounded-xl border-2 border-amber-300 shadow-[0_0_0_9999px_rgba(15,23,42,0.18)]"
                      style={{
                        left: `${(((visionTarget.bbox?.left ?? visionTarget.x ?? 0) as number) / imageMetrics.naturalWidth) * 100}%`,
                        top: `${(((visionTarget.bbox?.top ?? visionTarget.y ?? 0) as number) / imageMetrics.naturalHeight) * 100}%`,
                        width: `${(((visionTarget.bbox?.width ?? 28) as number) / imageMetrics.naturalWidth) * 100}%`,
                        height: `${(((visionTarget.bbox?.height ?? 28) as number) / imageMetrics.naturalHeight) * 100}%`,
                      }}
                    />
                    <div
                      className="absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white bg-amber-300 shadow-lg"
                      style={{
                        left: `${(((visionTarget.x ?? 0) as number) / imageMetrics.naturalWidth) * 100}%`,
                        top: `${(((visionTarget.y ?? 0) as number) / imageMetrics.naturalHeight) * 100}%`,
                      }}
                    />
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="flex h-64 items-center justify-center text-sm text-slate-300">No live screenshot yet.</div>
            )}
          </div>
        </div>
        {visionTarget ? (
          <div className="mt-3 rounded-[1.15rem] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            Vision target: {visionTarget.label || "target"}
            {typeof visionTarget.confidence === "number" ? ` (${visionTarget.confidence.toFixed(2)})` : ""}
            {visionTarget.requires_confirmation ? ' — waiting for "confirm" or "cancel".' : " — clicked with vision fallback."}
          </div>
        ) : null}
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(320px,1.05fr)]">
        <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel backdrop-blur">
          <div className="border-b border-line/70 pb-4">
            <p className="text-xs uppercase tracking-[0.24em] text-accent">Tabs</p>
            <h2 className="mt-2 font-display text-2xl text-ink">Open pages</h2>
          </div>
          <div className="mt-4 space-y-3 max-h-[34rem] overflow-y-auto pr-1">
            {tabs.length === 0 ? (
              <div className="rounded-[1.35rem] border border-dashed border-line/80 bg-foam/70 p-4 text-sm text-slate-500">
                No browser tabs are open yet.
              </div>
            ) : null}
            {tabs.map((tab) => (
              <div key={tab.tab_id} className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-ink">{tab.title || tab.url || tab.tab_id}</div>
                  <div className="rounded-full bg-glow px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-accent">
                    {tab.active ? `active${tab.mode ? ` • ${tab.mode}` : ""}` : tab.mode || tab.tab_id}
                  </div>
                </div>
                <div className="mt-2 break-all text-sm text-slate-600">{tab.url || "about:blank"}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-[2rem] border border-white/85 bg-gradient-to-br from-white to-foam p-5 shadow-panel">
          <div className="border-b border-line/70 pb-4">
            <p className="text-xs uppercase tracking-[0.24em] text-accent">Profiles & Activity</p>
            <h2 className="mt-2 font-display text-2xl text-ink">Sessions, downloads, and logs</h2>
          </div>
          <div className="mt-4 space-y-4">
            <div>
              <div className="mb-2 text-xs uppercase tracking-[0.18em] text-slate-500">Profiles</div>
              <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
                {profiles.slice(0, 6).map((profile) => (
                  <div key={profile.profile_key} className="rounded-[1rem] border border-line/80 bg-white/95 p-3 text-sm">
                    <div className="font-medium text-ink">{profile.site_name} / {profile.profile_name}</div>
                    <div className="text-slate-600">{profile.status} | {shortTime(profile.last_used_at)}</div>
                  </div>
                ))}
                {profiles.length === 0 ? <div className="text-sm text-slate-500">No saved browser profiles yet.</div> : null}
              </div>
            </div>

            <div>
              <div className="mb-2 text-xs uppercase tracking-[0.18em] text-slate-500">Downloads</div>
              <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
                {downloads.slice(0, 6).map((item) => (
                  <div key={`${item.path}-${item.created_at}`} className="rounded-[1rem] border border-line/80 bg-white/95 p-3 text-sm">
                    <div className="font-medium text-ink">{item.filename}</div>
                    <div className="text-slate-600">{shortTime(item.created_at)} | {item.size} bytes</div>
                  </div>
                ))}
                {downloads.length === 0 ? <div className="text-sm text-slate-500">No browser downloads yet.</div> : null}
              </div>
            </div>

            <div>
              <div className="mb-2 text-xs uppercase tracking-[0.18em] text-slate-500">Logs</div>
              <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                {logs.slice(0, 8).map((item) => (
                  <div key={`${item.timestamp}-${item.message}`} className="rounded-[1rem] border border-line/80 bg-white/95 p-3 text-sm">
                    <div className="font-medium text-ink">{item.kind} / {item.level}</div>
                    <div className="mt-1 text-slate-600">{item.message}</div>
                    <div className="mt-2 text-xs uppercase tracking-[0.18em] text-slate-500">{shortTime(item.timestamp)}</div>
                  </div>
                ))}
                {logs.length === 0 ? <div className="text-sm text-slate-500">No browser logs yet.</div> : null}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
