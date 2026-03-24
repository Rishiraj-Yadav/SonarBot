"use client";

import { useEffect, useState } from "react";

import { fetchJson } from "../lib/gateway_client";

type BrowserTab = {
  tab_id: string;
  title: string;
  url: string;
  active: boolean;
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
  current_tab_id: string | null;
  tabs: BrowserTab[];
  active_profile?: BrowserProfile | null;
  streaming: boolean;
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

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const [stateData, tabsData, logsData, downloadsData, profilesData] = await Promise.all([
          fetchJson<{ state: BrowserState }>("/api/browser/state"),
          fetchJson<{ tabs: BrowserTab[] }>("/api/browser/tabs"),
          fetchJson<{ logs: BrowserLog[] }>("/api/browser/logs?limit=8"),
          fetchJson<{ downloads: BrowserDownload[] }>("/api/browser/downloads?limit=8"),
          fetchJson<{ profiles: BrowserProfile[] }>("/api/browser/profiles"),
        ]);
        if (!mounted) {
          return;
        }
        setState(stateData.state ?? null);
        setTabs(tabsData.tabs ?? []);
        setLogs(logsData.logs ?? []);
        setDownloads(downloadsData.downloads ?? []);
        setProfiles(profilesData.profiles ?? []);
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

    void load();
    window.addEventListener("sonarbot:browser.state", onBrowserState);
    window.addEventListener("sonarbot:browser.log", onBrowserLog);
    window.addEventListener("sonarbot:browser.download", onBrowserDownload);
    window.addEventListener("sonarbot:browser.screenshot", onBrowserScreenshot);
    window.addEventListener("sonarbot:browser.session_expired", onSessionExpired);
    const timer = window.setInterval(() => void load(), 12000);

    return () => {
      mounted = false;
      window.clearInterval(timer);
      window.removeEventListener("sonarbot:browser.state", onBrowserState);
      window.removeEventListener("sonarbot:browser.log", onBrowserLog);
      window.removeEventListener("sonarbot:browser.download", onBrowserDownload);
      window.removeEventListener("sonarbot:browser.screenshot", onBrowserScreenshot);
      window.removeEventListener("sonarbot:browser.session_expired", onSessionExpired);
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
                  {state?.headless ? "headless" : "headed"}
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
            </div>
            <div className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4 text-sm leading-6 text-slate-600">
              Profiles stay named per site/account, downloads land in the workspace inbox, and stale sessions remain
              inspectable instead of being silently deleted.
            </div>
          </div>
          <div className="overflow-hidden rounded-[1.35rem] border border-line/80 bg-slate-950">
            {screenshot ? (
              <img src={screenshot} alt="Live browser" className="h-64 w-full object-cover" />
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
                    {tab.active ? "active" : tab.tab_id}
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
