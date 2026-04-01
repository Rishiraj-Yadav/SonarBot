"use client";

import { FormEvent, useEffect, useState } from "react";

import { fetchJson } from "../lib/gateway_client";

type CoworkerArtifact = {
  artifact_id: string;
  path: string;
  kind: string;
  label?: string;
  created_at?: string;
};

type CoworkerTask = {
  task_id: string;
  summary: string;
  request_text?: string;
  status: string;
  current_step_index: number;
  total_steps: number;
  current_attempt?: number;
  last_backend?: string;
  stop_reason?: string;
  transcript?: Array<Record<string, unknown>>;
  artifacts?: CoworkerArtifact[];
  pending_approval?: Record<string, unknown>;
  latest_state?: Record<string, unknown>;
  backend_health?: Record<string, unknown>;
};

type CoworkerTasksResponse = {
  enabled: boolean;
  tasks: CoworkerTask[];
};

type CoworkerTaskResponse = {
  ok: boolean;
  task: CoworkerTask;
};

function shortTime(value?: string) {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) {
    return value;
  }
  return parsed.toLocaleString();
}

function statusTone(status: string) {
  switch (status) {
    case "completed":
      return "bg-emerald-100 text-emerald-700";
    case "failed":
      return "bg-rose-100 text-rose-700";
    case "stopped":
      return "bg-amber-100 text-amber-700";
    default:
      return "bg-sand text-slate-700";
  }
}

export function CoworkerPanel() {
  const [tasks, setTasks] = useState<CoworkerTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>("");
  const [draftTask, setDraftTask] = useState("open the file you see on screen now");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const data = await fetchJson<CoworkerTasksResponse>("/api/coworker/tasks?limit=20");
        if (!mounted) {
          return;
        }
        const nextTasks = data.tasks ?? [];
        setTasks(nextTasks);
        setSelectedTaskId((current) => {
          if (current && nextTasks.some((task) => task.task_id === current)) {
            return current;
          }
          return nextTasks[0]?.task_id ?? "";
        });
      } catch {
        return;
      }
    };

    void load();
    const timer = window.setInterval(() => void load(), 8000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);

  const selectedTask = tasks.find((task) => task.task_id === selectedTaskId) ?? tasks[0];

  async function refreshTask(taskId: string) {
    const response = await fetchJson<CoworkerTaskResponse>(`/api/coworker/tasks/${encodeURIComponent(taskId)}`);
    if (!response.ok) {
      return;
    }
    setTasks((current) => {
      const remaining = current.filter((item) => item.task_id !== response.task.task_id);
      return [response.task, ...remaining];
    });
    setSelectedTaskId(response.task.task_id);
  }

  async function runTask(event: FormEvent) {
    event.preventDefault();
    const task = draftTask.trim();
    if (!task) {
      return;
    }
    setBusy(true);
    try {
      const response = await fetch("http://localhost:8765/api/coworker/tasks/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task }),
      });
      const payload = (await response.json()) as CoworkerTaskResponse;
      if (payload.ok) {
        setTasks((current) => [payload.task, ...current.filter((item) => item.task_id !== payload.task.task_id)]);
        setSelectedTaskId(payload.task.task_id);
      }
    } finally {
      setBusy(false);
    }
  }

  async function planTask() {
    const task = draftTask.trim();
    if (!task) {
      return;
    }
    setBusy(true);
    try {
      const response = await fetch("http://localhost:8765/api/coworker/tasks/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task }),
      });
      const payload = (await response.json()) as CoworkerTaskResponse;
      if (payload.ok) {
        setTasks((current) => [payload.task, ...current.filter((item) => item.task_id !== payload.task.task_id)]);
        setSelectedTaskId(payload.task.task_id);
      }
    } finally {
      setBusy(false);
    }
  }

  async function taskControl(path: string) {
    if (!selectedTask) {
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`http://localhost:8765${path}`, { method: "POST" });
      const payload = (await response.json()) as CoworkerTaskResponse;
      if (payload.ok) {
        setTasks((current) => [payload.task, ...current.filter((item) => item.task_id !== payload.task.task_id)]);
        setSelectedTaskId(payload.task.task_id);
      } else {
        await refreshTask(selectedTask.task_id);
      }
    } finally {
      setBusy(false);
    }
  }

  const artifacts = selectedTask?.artifacts ?? [];
  const transcript = selectedTask?.transcript ?? [];
  const pendingApproval = selectedTask?.pending_approval ?? {};

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,0.8fr)]">
      <section className="rounded-[2rem] border border-white/85 bg-white/90 p-5 shadow-panel">
        <div className="border-b border-line/70 pb-4">
          <p className="text-xs uppercase tracking-[0.24em] text-accent">Coworker Control</p>
          <h2 className="mt-2 font-display text-3xl text-ink">Verified visual task loop</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Run screenshot-driven tasks, inspect the latest retry decision, and review the evidence SonarBot used before it continued or stopped.
          </p>
        </div>

        <form onSubmit={runTask} className="mt-4 rounded-[1.5rem] border border-line/80 bg-foam/70 p-4">
          <label className="text-xs uppercase tracking-[0.22em] text-slate-500">New coworker task</label>
          <textarea
            value={draftTask}
            onChange={(event) => setDraftTask(event.target.value)}
            className="mt-3 min-h-28 w-full rounded-[1.2rem] border border-line/80 bg-white px-4 py-3 text-sm text-slate-700 outline-none"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="submit" disabled={busy} className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white">
              Run
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void planTask()}
              className="rounded-full bg-blue-100 px-4 py-2 text-sm font-medium text-blue-700"
            >
              Plan
            </button>
            {selectedTask ? (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void taskControl(`/api/coworker/tasks/${encodeURIComponent(selectedTask.task_id)}/step`)}
                  className="rounded-full bg-sand px-4 py-2 text-sm font-medium text-slate-700"
                >
                  Continue
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void taskControl(`/api/coworker/tasks/${encodeURIComponent(selectedTask.task_id)}/retry`)}
                  className="rounded-full bg-amber-100 px-4 py-2 text-sm font-medium text-amber-700"
                >
                  Retry
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void taskControl(`/api/coworker/tasks/${encodeURIComponent(selectedTask.task_id)}/stop`)}
                  className="rounded-full bg-rose-100 px-4 py-2 text-sm font-medium text-rose-700"
                >
                  Stop
                </button>
              </>
            ) : null}
          </div>
        </form>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.8fr)]">
          <div className="rounded-[1.5rem] border border-line/80 bg-white/95 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Active task</div>
                <h3 className="mt-2 text-lg font-semibold text-ink">{selectedTask?.summary ?? "No coworker task selected"}</h3>
              </div>
              {selectedTask ? (
                <div className={`rounded-full px-3 py-2 text-[11px] uppercase tracking-[0.18em] ${statusTone(selectedTask.status)}`}>
                  {selectedTask.status}
                </div>
              ) : null}
            </div>
            {selectedTask ? (
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-[1.2rem] bg-foam/70 p-3">
                  <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Progress</div>
                  <div className="mt-2 text-sm text-slate-700">
                    {selectedTask.current_step_index}/{selectedTask.total_steps} step(s)
                  </div>
                </div>
                <div className="rounded-[1.2rem] bg-foam/70 p-3">
                  <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Backend</div>
                  <div className="mt-2 text-sm text-slate-700">{selectedTask.last_backend || "pending"}</div>
                </div>
                <div className="rounded-[1.2rem] bg-foam/70 p-3">
                  <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Attempt</div>
                  <div className="mt-2 text-sm text-slate-700">{selectedTask.current_attempt || 0}</div>
                </div>
                <div className="rounded-[1.2rem] bg-foam/70 p-3">
                  <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Stop reason</div>
                  <div className="mt-2 text-sm text-slate-700">{selectedTask.stop_reason || "none"}</div>
                </div>
              </div>
            ) : null}

            <div className="mt-4">
              <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Screenshot timeline</div>
              {artifacts.length === 0 ? (
                <div className="mt-3 rounded-[1.2rem] border border-dashed border-line/80 bg-foam/70 p-4 text-sm text-slate-500">
                  No coworker screenshots have been stored for this task yet.
                </div>
              ) : (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {artifacts.map((artifact) => (
                    <a
                      key={artifact.artifact_id}
                      href={`http://localhost:8765/api/coworker/tasks/${encodeURIComponent(selectedTask.task_id)}/artifacts/${encodeURIComponent(artifact.artifact_id)}`}
                      target="_blank"
                      rel="noreferrer"
                      className="overflow-hidden rounded-[1.15rem] border border-line/80 bg-white shadow-sm"
                    >
                      <img
                        src={`http://localhost:8765/api/coworker/tasks/${encodeURIComponent(selectedTask.task_id)}/artifacts/${encodeURIComponent(artifact.artifact_id)}`}
                        alt={artifact.label ?? artifact.kind}
                        className="h-36 w-full object-cover"
                      />
                      <div className="p-3">
                        <div className="text-sm font-medium text-ink">{artifact.label || artifact.kind}</div>
                        <div className="mt-1 text-[11px] uppercase tracking-[0.18em] text-slate-500">{shortTime(artifact.created_at)}</div>
                      </div>
                    </a>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="space-y-4">
            <section className="rounded-[1.5rem] border border-line/80 bg-gradient-to-br from-white to-foam p-4">
              <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Pending approval</div>
              {Object.keys(pendingApproval).length === 0 ? (
                <div className="mt-3 text-sm text-slate-600">No host approval is currently blocking this task.</div>
              ) : (
                <div className="mt-3 space-y-2 text-sm text-slate-700">
                  <div>{String(pendingApproval.action_kind ?? "desktop action")}</div>
                  <div>{String(pendingApproval.target_summary ?? "")}</div>
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">
                    Status: {String(pendingApproval.status ?? "pending")}
                  </div>
                </div>
              )}
            </section>

            <section className="rounded-[1.5rem] border border-line/80 bg-white/95 p-4">
              <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Backend health</div>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-slate-600">
                {JSON.stringify(selectedTask?.backend_health ?? {}, null, 2)}
              </pre>
            </section>
          </div>
        </div>
      </section>

      <section className="rounded-[2rem] border border-white/85 bg-gradient-to-br from-white to-foam p-5 shadow-panel">
        <div className="border-b border-line/70 pb-4">
          <p className="text-xs uppercase tracking-[0.24em] text-accent">Coworker History</p>
          <h2 className="mt-2 font-display text-2xl text-ink">Recent task transcript</h2>
        </div>
        <div className="mt-4 space-y-3">
          <div className="max-h-[18rem] space-y-3 overflow-y-auto pr-1">
            {tasks.length === 0 ? (
              <div className="rounded-[1.35rem] border border-dashed border-line/80 bg-white/80 p-4 text-sm text-slate-500">
                No coworker tasks yet.
              </div>
            ) : null}
            {tasks.map((task) => (
              <button
                key={task.task_id}
                type="button"
                onClick={() => setSelectedTaskId(task.task_id)}
                className={`w-full rounded-[1.3rem] border p-4 text-left transition ${
                  task.task_id === selectedTask?.task_id
                    ? "border-accent/40 bg-white shadow-card"
                    : "border-line/80 bg-white/90 hover:border-line"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-ink">{task.summary}</div>
                    <div className="mt-1 text-[11px] uppercase tracking-[0.18em] text-slate-500">{task.task_id}</div>
                  </div>
                  <div className={`rounded-full px-2 py-1 text-[10px] uppercase tracking-[0.18em] ${statusTone(task.status)}`}>
                    {task.status}
                  </div>
                </div>
                <div className="mt-3 text-xs text-slate-500">
                  {task.current_step_index}/{task.total_steps} step(s)
                  {task.last_backend ? ` | ${task.last_backend}` : ""}
                  {task.current_attempt ? ` | attempt ${task.current_attempt}` : ""}
                </div>
              </button>
            ))}
          </div>

          <div className="rounded-[1.4rem] border border-line/80 bg-white/95 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Why it continued or stopped</div>
            <div className="mt-3 max-h-[18rem] space-y-3 overflow-y-auto pr-1">
              {transcript.length === 0 ? (
                <div className="text-sm text-slate-500">No transcript yet for the selected task.</div>
              ) : null}
              {transcript.map((entry, index) => (
                <div key={`${selectedTask?.task_id ?? "task"}-${index}`} className="rounded-[1.1rem] bg-foam/70 p-3">
                  <div className="text-sm font-medium text-ink">{String(entry.title ?? entry.step_type ?? "step")}</div>
                  <div className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-500">
                    {String(entry.status ?? "unknown")}
                    {entry.last_backend ? ` | ${String(entry.last_backend)}` : ""}
                    {entry.current_attempt ? ` | attempt ${String(entry.current_attempt)}` : ""}
                  </div>
                  <div className="mt-2 text-sm leading-6 text-slate-600">{String(entry.summary ?? "")}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
