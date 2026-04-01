"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

type NavItem = {
  href: string;
  label: string;
  eyebrow: string;
  description: string;
};

const navGroups: Array<{ title: string; items: NavItem[] }> = [
  {
    title: "Workspace",
    items: [
      {
        href: "/",
        label: "Console",
        eyebrow: "Live chat",
        description: "Talk to SonarBot, use slash commands, and run tool-driven workflows.",
      },
      {
        href: "/sessions",
        label: "Sessions",
        eyebrow: "History",
        description: "Review the active thread and scroll through recent conversation state.",
      },
      {
        href: "/skills",
        label: "Skills",
        eyebrow: "Capabilities",
        description: "Inspect bundled skills, aliases, and natural-language activation state.",
      },
    ],
  },
  {
    title: "Operations",
    items: [
      {
        href: "/browser",
        label: "Browser",
        eyebrow: "Playwright",
        description: "Profiles, tabs, downloads, logs, and live headed-browser snapshots.",
      },
      {
        href: "/automation",
        label: "Automation",
        eyebrow: "Background runs",
        description: "Cron jobs, notifications, and live rule state in one focused surface.",
      },
      {
        href: "/coworker",
        label: "Coworker",
        eyebrow: "Visual loop",
        description: "Track screenshot-driven desktop tasks, retries, backend health, and verification history.",
      },
      {
        href: "/host-access",
        label: "Host access",
        eyebrow: "Approvals",
        description: "Review pending host actions, approvals, backups, and recent audit trails.",
      },
    ],
  },
  {
    title: "Control",
    items: [
      {
        href: "/settings",
        label: "Settings",
        eyebrow: "Runtime",
        description: "Inspect the current backend configuration snapshot exposed by the gateway.",
      },
    ],
  },
];

const mobileItems = navGroups.flatMap((group) => group.items);

function isActive(pathname: string, href: string) {
  if (href === "/") {
    return pathname === "/";
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="mx-auto max-w-[1680px]">
      <div className="grid gap-5 xl:grid-cols-[296px_minmax(0,1fr)]">
        <aside className="hidden xl:block">
          <div className="sticky top-6 h-[calc(100vh-3rem)] rounded-[2.1rem] border border-white/10 bg-gradient-to-b from-slate-950 via-[#102a54] to-[#123d86] p-5 text-white shadow-panel">
            <div className="flex h-full flex-col">
              <div className="rounded-[1.7rem] border border-white/10 bg-white/5 p-5 backdrop-blur">
                <div className="text-xs uppercase tracking-[0.32em] text-sky-200">SonarBot</div>
                <h1 className="mt-3 font-display text-[2.35rem] leading-none">Control stack</h1>
                <p className="mt-3 text-sm leading-6 text-sky-50/78">
                  Cleanly separated workspaces for chat, browser automation, host actions, session review, and skill
                  control.
                </p>
              </div>

              <nav className="mt-5 flex-1 space-y-5 overflow-y-auto pr-1">
                {navGroups.map((group) => (
                  <div key={group.title}>
                    <div className="mb-2 px-2 text-[11px] uppercase tracking-[0.28em] text-white/45">{group.title}</div>
                    <div className="space-y-2">
                      {group.items.map((item) => {
                        const active = isActive(pathname, item.href);
                        return (
                          <Link
                            key={item.href}
                            href={item.href}
                            className={`block rounded-[1.45rem] border px-4 py-4 transition ${
                              active
                                ? "border-white/40 bg-white text-slate-900 shadow-card"
                                : "border-white/10 bg-white/5 text-white/92 hover:border-white/20 hover:bg-white/10"
                            }`}
                          >
                            <div className={`text-[11px] uppercase tracking-[0.28em] ${active ? "text-accent" : "text-sky-200/70"}`}>
                              {item.eyebrow}
                            </div>
                            <div className="mt-2 text-lg font-semibold">{item.label}</div>
                            <p className={`mt-2 text-sm leading-6 ${active ? "text-slate-600" : "text-white/72"}`}>
                              {item.description}
                            </p>
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </nav>

              <div className="mt-5 grid gap-3">
                <div className="rounded-[1.45rem] border border-white/10 bg-white/5 p-4">
                  <div className="text-[11px] uppercase tracking-[0.28em] text-sky-200/70">Connected surfaces</div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-white/85">
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1">WebSocket chat</span>
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1">Browser runtime</span>
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1">Automation inbox</span>
                    <span className="rounded-full border border-white/10 bg-white/10 px-3 py-1">Host approvals</span>
                  </div>
                </div>
                <div className="rounded-[1.45rem] border border-white/10 bg-white/5 p-4 text-sm leading-6 text-white/78">
                  Keep the Console tab focused on conversation. Use the operation tabs when you want detail, history, or
                  control over a specific subsystem.
                </div>
              </div>
            </div>
          </div>
        </aside>

        <div className="min-w-0 py-4 xl:py-6">
          <div className="mb-4 flex gap-2 overflow-x-auto pb-2 xl:hidden">
            {mobileItems.map((item) => {
              const active = isActive(pathname, item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`whitespace-nowrap rounded-full border px-4 py-2 text-sm transition ${
                    active
                      ? "border-accent bg-accent text-white"
                      : "border-white/80 bg-white/90 text-slate-700 shadow-sm"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
