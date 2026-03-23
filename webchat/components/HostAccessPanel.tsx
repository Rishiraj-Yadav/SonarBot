"use client";

import { useEffect, useState } from "react";

import { fetchJson } from "../lib/gateway_client";

type HostApproval = {
  approval_id: string;
  action_kind: string;
  target_summary: string;
  category: string;
  status: string;
  created_at: string;
  expires_at: string;
};

type HostAuditEntry = {
  audit_id: string;
  action_kind: string;
  target: string;
  outcome: string;
  approval_mode: string;
  timestamp: string;
  backup_id?: string | null;
};

type ApprovalsResponse = {
  approvals: HostApproval[];
};

type AuditResponse = {
  entries: HostAuditEntry[];
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

export function HostAccessPanel() {
  const [approvals, setApprovals] = useState<HostApproval[]>([]);
  const [entries, setEntries] = useState<HostAuditEntry[]>([]);

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const [approvalData, auditData] = await Promise.all([
          fetchJson<ApprovalsResponse>("/api/system-access/approvals?limit=8"),
          fetchJson<AuditResponse>("/api/system-access/audit?limit=8"),
        ]);
        if (!mounted) {
          return;
        }
        setApprovals(approvalData.approvals ?? []);
        setEntries(auditData.entries ?? []);
      } catch {
        return;
      }
    };

    const onApprovalEvent = () => {
      void load();
    };

    window.addEventListener("sonarbot:host-approval", onApprovalEvent);
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
      window.removeEventListener("sonarbot:host-approval", onApprovalEvent);
    };
  }, []);

  async function decide(approvalId: string, decision: "approved" | "rejected") {
    await fetch(`http://localhost:8765/api/system-access/approvals/${approvalId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    setApprovals((current) =>
      current.map((item) => (item.approval_id === approvalId ? { ...item, status: decision } : item)),
    );
  }

  async function restore(backupId: string) {
    await fetch(`http://localhost:8765/api/system-access/audit/${backupId}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  }

  return (
    <aside className="space-y-4">
      <section className="rounded-[2rem] border border-white/80 bg-white/88 p-4 shadow-panel backdrop-blur">
        <div className="border-b border-line/70 pb-4">
          <p className="text-xs uppercase tracking-[0.24em] text-accent">Host Access</p>
          <h2 className="mt-2 font-display text-3xl text-ink">Pending approvals</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Risky host-system commands and file changes pause here until you approve or deny them.
          </p>
        </div>
        <div className="mt-4 space-y-3 max-h-[20rem] overflow-y-auto pr-1">
          {approvals.length === 0 ? (
            <div className="rounded-[1.35rem] border border-dashed border-line/80 bg-foam/70 p-4 text-sm text-slate-500">
              No host approvals are waiting right now.
            </div>
          ) : null}
          {approvals.map((approval) => (
            <div key={approval.approval_id} className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-2">
                  <div className="text-sm font-semibold text-ink">{approval.action_kind}</div>
                  <div className="text-sm leading-6 text-slate-600">{approval.target_summary}</div>
                  <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">
                    {approval.category} • {shortTime(approval.created_at)}
                  </div>
                </div>
                <div className="rounded-full bg-sand px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-slate-600">
                  {approval.status}
                </div>
              </div>
              {approval.status === "pending" ? (
                <div className="mt-4 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void decide(approval.approval_id, "approved")}
                    className="rounded-full bg-emerald-100 px-3 py-2 text-xs font-medium text-emerald-700"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => void decide(approval.approval_id, "rejected")}
                    className="rounded-full bg-rose-100 px-3 py-2 text-xs font-medium text-rose-700"
                  >
                    Deny
                  </button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-[2rem] border border-white/80 bg-gradient-to-br from-white to-foam p-4 shadow-panel">
        <div className="border-b border-line/70 pb-4">
          <p className="text-xs uppercase tracking-[0.24em] text-accent">Audit Trail</p>
          <h2 className="mt-2 font-display text-2xl text-ink">Recent host actions</h2>
        </div>
        <div className="mt-4 space-y-3 max-h-[18rem] overflow-y-auto pr-1">
          {entries.length === 0 ? (
            <div className="rounded-[1.35rem] border border-dashed border-line/80 bg-white/80 p-4 text-sm text-slate-500">
              No host actions have been recorded yet.
            </div>
          ) : null}
          {entries.map((entry) => (
            <div key={entry.audit_id} className="rounded-[1.35rem] border border-line/80 bg-white/95 p-4 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-ink">{entry.action_kind}</div>
                  <div className="mt-1 text-sm leading-6 text-slate-600">{entry.target}</div>
                  <div className="mt-2 text-[11px] uppercase tracking-[0.18em] text-slate-400">
                    {entry.approval_mode} • {shortTime(entry.timestamp)}
                  </div>
                </div>
                <div className="space-y-2 text-right">
                  <div className="rounded-full bg-sand px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-slate-600">
                    {entry.outcome}
                  </div>
                  {entry.backup_id ? (
                    <button
                      type="button"
                      onClick={() => void restore(entry.backup_id!)}
                      className="rounded-full bg-glow px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-accent"
                    >
                      Restore
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </aside>
  );
}
