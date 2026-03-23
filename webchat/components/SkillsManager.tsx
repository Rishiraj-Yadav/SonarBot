"use client";

import { useEffect, useState } from "react";

type Skill = {
  name: string;
  description: string;
  enabled: boolean;
  eligible: boolean;
  user_invocable: boolean;
  natural_language_enabled: boolean;
  aliases: string[];
};

export function SkillsManager() {
  const [skills, setSkills] = useState<Skill[]>([]);

  useEffect(() => {
    fetch("http://localhost:8765/api/skills")
      .then((response) => response.json())
      .then((data) => setSkills(data.skills ?? []))
      .catch(() => undefined);
  }, []);

  async function toggle(name: string) {
    const response = await fetch(`http://localhost:8765/api/skills/${encodeURIComponent(name)}/toggle`, {
      method: "POST",
    });
    const updated = await response.json();
    setSkills((current) => current.map((skill) => (skill.name === name ? { ...skill, enabled: updated.enabled } : skill)));
  }

  return (
    <div className="space-y-4">
      {skills.map((skill) => (
        <div key={skill.name} className="rounded-3xl border border-line bg-white p-5 shadow-card">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-lg font-semibold">{skill.name}</h3>
              <p className="mt-2 text-sm text-slate-600">{skill.description}</p>
              <p className="mt-2 text-xs uppercase tracking-[0.16em] text-slate-500">
                {skill.eligible ? "Eligible" : "Unavailable in this environment"}
              </p>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                {skill.user_invocable ? (
                  <span className="rounded-full bg-accent/10 px-3 py-1 text-accent">Slash</span>
                ) : null}
                {skill.natural_language_enabled ? (
                  <span className="rounded-full bg-emerald-100 px-3 py-1 text-emerald-700">Natural language</span>
                ) : null}
                {skill.aliases?.map((alias) => (
                  <span key={alias} className="rounded-full bg-slate-100 px-3 py-1 text-slate-600">
                    {alias}
                  </span>
                ))}
              </div>
            </div>
            <button
              className={`rounded-full px-4 py-2 text-sm ${skill.enabled ? "bg-accent text-white" : "bg-slate-200 text-slate-700"}`}
              onClick={() => toggle(skill.name)}
              type="button"
            >
              {skill.enabled ? "Enabled" : "Disabled"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
