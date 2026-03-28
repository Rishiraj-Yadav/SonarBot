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
  tabs: BrowserTab[];
  active_profile?: BrowserProfile | null;
  streaming: boolean;
  pending_protected_action?: {
    action_type?: string;
    selector?: string;
    target?: string;
  } | null;
};

type BrowserWorkflow = {
  recipe_name?: string;
  status?: string;
  response_text?: string;
  message?: string;
};

type BrowserScreenshot = {
  image_data_url?: string;
  tab_id?: string;
  url?: string;
  title?: string;
  mode?: string;
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

export function BrowserPanel() {
  const [state, setState] = useState<BrowserState | null>(null);
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [logs, setLogs] = useState<BrowserLog[]>([]);
  const [downloads, setDownloads] = useState<BrowserDownload[]>([]);
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [screenshot, setScreenshot] = useState("");
  const [workflow, setWorkflow] = useState<BrowserWorkflow | null>(null);
  const screenshotRef = useRef<HTMLImageElement | null>(null);

  const fetchBrowserData = async () => {
    const [stateData, tabsData, logsData, downloadsData, profilesData, screenshotData] = await Promise.all([
      fetchJson<{ state: BrowserState }>("/api/browser/state"),
      fetchJson<{ tabs: BrowserTab[] }>("/api/browser/tabs"),
      fetchJson<{ logs: BrowserLog[] }>("/api/browser/logs?limit=8"),
      fetchJson<{ downloads: BrowserDownload[] }>("/api/browser/downloads?limit=8"),
      fetchJson<{ profiles: BrowserProfile[] }>("/api/browser/profiles"),
      fetchJson<{ screenshot: BrowserScreenshot | null }>("/api/browser/live-screenshot"),
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
    if (screenshotData.screenshot?.image_data_url) {
      setScreenshot(screenshotData.screenshot.image_data_url);
    }
  };

  const handleScreenshotClick = async (event: MouseEvent<HTMLImageElement>) => {
    const element = screenshotRef.current;
    if (!element || !state?.current_tab_id) {
      return;
    }
    const bounds = element.getBoundingClientRect();
    if (!bounds.width || !bounds.height || !element.naturalWidth || !element.naturalHeight) {
      return;
    }
    const x = Math.round(((event.clientX - bounds.left) / bounds.width) * element.naturalWidth);
    const y = Math.round(((event.clientY - bounds.top) / bounds.height) * element.naturalHeight);
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

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const data = await fetchBrowserData();
        if (!mounted) {
          return;
        }
        applyBrowserData(data);
      } catch {
        return;
      }
    };

    const onBrowserState = (event: Event) => {
      const detail = (event as CustomEvent<BrowserState>).detail;
      if (detail) {
        setState(detail);
        setTabs(detail.tabs ?? []);
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

    const onSessionExpired = () => {
      void load();
    };

    const onWorkflowUpdate = (event: Event) => {
      const detail = (event as CustomEvent<BrowserWorkflow>).detail;
      if (detail) {
        setWorkflow(detail);
      }
    };

    void load();
    window.addEventListener("sonarbot:browser.state", onBrowserState);
    window.addEventListener("sonarbot:browser.log", onBrowserLog);
    window.addEventListener("sonarbot:browser.download", onBrowserDownload);
    window.addEventListener("sonarbot:browser.screenshot", onBrowserScreenshot);
    window.addEventListener("sonarbot:browser.session_expired", onSessionExpired);
    window.addEventListener("sonarbot:browser.workflow.started", onWorkflowUpdate);
    window.addEventListener("sonarbot:browser.workflow.step", onWorkflowUpdate);
    window.addEventListener("sonarbot:browser.workflow.blocked", onWorkflowUpdate);
    window.addEventListener("sonarbot:browser.workflow.completed", onWorkflowUpdate);
    const timer = window.setInterval(() => void load(), 12000);

    return () => {
      mounted = false;
      window.clearInterval(timer);
      window.removeEventListener("sonarbot:browser.state", onBrowserState);
      window.removeEventListener("sonarbot:browser.log", onBrowserLog);
      window.removeEventListener("sonarbot:browser.download", onBrowserDownload);
      window.removeEventListener("sonarbot:browser.screenshot", onBrowserScreenshot);
      window.removeEventListener("sonarbot:browser.session_expired", onSessionExpired);
      window.removeEventListener("sonarbot:browser.workflow.started", onWorkflowUpdate);
      window.removeEventListener("sonarbot:browser.workflow.step", onWorkflowUpdate);
      window.removeEventListener("sonarbot:browser.workflow.blocked", onWorkflowUpdate);
      window.removeEventListener("sonarbot:browser.workflow.completed", onWorkflowUpdate);
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
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Workflow</div>
              {workflow ? (
                <>
                  <div className="mt-2 text-sm font-medium text-ink">{workflow.recipe_name || "Browser workflow"}</div>
                  <div className="mt-1 text-sm text-slate-600">{workflow.response_text || workflow.message || "Waiting for workflow updates."}</div>
                  <div className="mt-2 text-xs uppercase tracking-[0.18em] text-slate-500">
                    {workflow.status || "running"}
                  </div>
                </>
              ) : (
                <div className="mt-2 text-sm text-slate-500">No browser workflow has run yet.</div>
              )}
            </div>
          </div>
          <div className="overflow-hidden rounded-[1.35rem] border border-line/80 bg-slate-950">
            {screenshot ? (
              <img
                ref={screenshotRef}
                src={screenshot}
                alt="Live browser"
                className="h-64 w-full cursor-crosshair object-cover"
                onClick={handleScreenshotClick}
              />
            ) : (
              <div className="flex h-64 items-center justify-center text-sm text-slate-300">No live screenshot yet.</div>
            )}
          </div>
        </div>
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
