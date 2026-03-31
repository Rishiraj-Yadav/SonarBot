"use client";

import { useEffect, useState } from "react";

import { fetchJson, gatewayFetch } from "../lib/gateway_client";

type NotificationItem = {
  notification_id: string;
  title: string;
  body: string;
  source: string;
  severity: string;
  status: string;
  created_at: string;
};

type AutomationRun = {
  run_id: string;
  rule_name: string;
  status: string;
  created_at: string;
};

type AutomationRule = {
  name: string;
  trigger: string;
  paused: boolean;
};

type NotificationsResponse = {
  notifications: NotificationItem[];
};

type RunsResponse = {
  runs: AutomationRun[];
};

type RulesResponse = {
  rules: AutomationRule[];
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

function bodyPreview(title: string, body: string) {
  const normalizedTitle = title.trim();
  const normalizedBody = body.trim();
  if (!normalizedBody) {
    return "";
  }
  if (normalizedBody === normalizedTitle) {
    return "";
  }
  return normalizedBody;
}

export function AutomationPanel() {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [rules, setRules] = useState<AutomationRule[]>([]);

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const [notificationData, runData, ruleData] = await Promise.all([
          fetchJson<NotificationsResponse>("/api/notifications?limit=8"),
          fetchJson<RunsResponse>("/api/automation/runs?limit=8"),
          fetchJson<RulesResponse>("/api/automation/rules"),
        ]);
        if (!mounted) {
          return;
        }
        setNotifications(notificationData.notifications ?? []);
        setRuns(runData.runs ?? []);
        setRules(ruleData.rules ?? []);
      } catch {
        return;
      }
    };

    const onNotification = (event: Event) => {
      const detail = (event as CustomEvent<Partial<NotificationItem>>).detail;
      if (!detail) {
        return;
      }
      const created = {
        notification_id: detail.notification_id ?? crypto.randomUUID(),
        title: detail.title ?? "Automation update",
        body: detail.body ?? "",
        source: detail.source ?? "automation",
        severity: detail.severity ?? "info",
        status: detail.status ?? "delivered",
        created_at: detail.created_at ?? new Date().toISOString(),
      } satisfies NotificationItem;
      setNotifications((current) =>
        [created, ...current.filter((item) => item.notification_id !== created.notification_id)].slice(0, 8),
      );
      void load();
    };

    void load();
    window.addEventListener("sonarbot:notification", onNotification);
    const timer = window.setInterval(() => void load(), 15000);

    return () => {
      mounted = false;
      window.clearInterval(timer);
      window.removeEventListener("sonarbot:notification", onNotification);
    };
  }, []);

  async function toggleRule(rule: AutomationRule) {
    const path = rule.paused ? `/api/automation/rules/${rule.name}/resume` : `/api/automation/rules/${rule.name}/pause`;
    try {
      await gatewayFetch(path, { method: "POST" });
      setRules((current) => current.map((item) => (item.name === rule.name ? { ...item, paused: !item.paused } : item)));
    } catch {
      return;
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.18fr)_minmax(320px,0.82fr)]">
      <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel backdrop-blur">
        <div className="border-b border-line/70 pb-4">
          <p className="text-xs uppercase tracking-[0.24em] text-accent">Automation Inbox</p>
          <h2 className="mt-2 font-display text-3xl text-ink">Recent notifications</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Background cron, heartbeat, and webhook runs land here even when they are primarily delivered elsewhere.
          </p>
        </div>
        <div className="mt-4 space-y-3 max-h-[42rem] overflow-y-auto pr-1">
          {notifications.length === 0 ? (
            <div className="rounded-[1.35rem] border border-dashed border-line/80 bg-foam/70 p-4 text-sm text-slate-500">
              No automation notifications yet.
            </div>
          ) : null}
          {notifications.map((item) => (
            <div key={item.notification_id} className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-2">
                  <div className="text-sm font-semibold text-ink">{item.title}</div>
                  <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-slate-400">
                    <span>{item.source}</span>
                    <span>|</span>
                    <span>{shortTime(item.created_at)}</span>
                  </div>
                </div>
                <div className="flex flex-col items-end gap-2">
                  <div className="rounded-full bg-glow px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-accent">
                    {item.severity}
                  </div>
                  <div className="rounded-full bg-sand px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-slate-600">
                    {item.status}
                  </div>
                </div>
              </div>
              {bodyPreview(item.title, item.body) ? (
                <p className="mt-3 line-clamp-4 text-sm leading-6 text-slate-600">{bodyPreview(item.title, item.body)}</p>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <div className="space-y-4">
        <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel backdrop-blur">
          <div className="border-b border-line/70 pb-4">
            <p className="text-xs uppercase tracking-[0.24em] text-accent">Automation Runs</p>
            <h2 className="mt-2 font-display text-2xl text-ink">Background activity</h2>
          </div>
          <div className="mt-4 space-y-3 max-h-[20rem] overflow-y-auto pr-1">
            {runs.length === 0 ? (
              <div className="rounded-[1.35rem] border border-dashed border-line/80 bg-foam/70 p-4 text-sm text-slate-500">
                No automation runs recorded yet.
              </div>
            ) : null}
            {runs.map((run) => (
              <div key={run.run_id} className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-ink">{run.rule_name}</div>
                  <div className="rounded-full bg-sand px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-slate-600">
                    {run.status}
                  </div>
                </div>
                <div className="mt-2 text-xs uppercase tracking-[0.18em] text-slate-500">{shortTime(run.created_at)}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-[2rem] border border-white/85 bg-gradient-to-br from-white to-foam p-5 shadow-panel">
          <div className="border-b border-line/70 pb-4">
            <p className="text-xs uppercase tracking-[0.24em] text-accent">Rule Controls</p>
            <h2 className="mt-2 font-display text-2xl text-ink">Live rule state</h2>
          </div>
          <div className="mt-4 space-y-3 max-h-[20rem] overflow-y-auto pr-1">
            {rules.length === 0 ? (
              <div className="rounded-[1.35rem] border border-dashed border-line/80 bg-white/80 p-4 text-sm text-slate-500">
                No automation rules are currently loaded.
              </div>
            ) : null}
            {rules.map((rule) => (
              <div key={rule.name} className="flex items-center justify-between gap-3 rounded-[1.35rem] border border-line/80 bg-white/95 p-4">
                <div>
                  <div className="text-sm font-medium text-ink">{rule.name}</div>
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{rule.trigger}</div>
                </div>
                <button
                  type="button"
                  onClick={() => void toggleRule(rule)}
                  className={`rounded-full px-3 py-2 text-xs font-medium ${
                    rule.paused ? "bg-emerald-100 text-emerald-700" : "bg-sand text-slate-700"
                  }`}
                >
                  {rule.paused ? "Resume" : "Pause"}
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
