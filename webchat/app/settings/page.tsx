async function getSettings() {
  try {
    const response = await fetch("http://localhost:8765/api/settings", { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    return response.json();
  } catch {
    return null;
  }
}

export default async function SettingsPage() {
  const settings = await getSettings();
  return (
    <main className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.2em] text-accent">Configuration</p>
        <h1 className="text-4xl font-semibold">Settings</h1>
      </header>
      <section className="rounded-3xl border border-line bg-white p-5 shadow-card">
        <pre className="overflow-x-auto whitespace-pre-wrap text-sm leading-6 text-slate-700">
          {JSON.stringify(settings ?? { status: "Gateway unavailable" }, null, 2)}
        </pre>
      </section>
    </main>
  );
}
