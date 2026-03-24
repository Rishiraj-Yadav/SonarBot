type HeroBadge = {
  label: string;
  value: string;
};

type WorkspaceHeroProps = {
  eyebrow: string;
  title: string;
  description: string;
  badges: HeroBadge[];
};

export function WorkspaceHero({ eyebrow, title, description, badges }: WorkspaceHeroProps) {
  return (
    <header className="rounded-[2.3rem] border border-white/85 bg-white/88 px-6 py-6 shadow-panel backdrop-blur sm:px-8">
      <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
        <div className="max-w-3xl">
          <p className="text-xs uppercase tracking-[0.32em] text-accent">{eyebrow}</p>
          <h1 className="mt-3 font-display text-4xl leading-[0.92] text-ink sm:text-5xl">{title}</h1>
          <p className="mt-4 max-w-2xl text-sm leading-7 text-slate-600 sm:text-base">{description}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {badges.map((badge) => (
            <div key={badge.label} className="rounded-[1.45rem] border border-line/70 bg-foam/85 px-4 py-3 text-sm shadow-sm">
              <div className="text-[11px] uppercase tracking-[0.22em] text-accent">{badge.label}</div>
              <div className="mt-2 text-slate-700">{badge.value}</div>
            </div>
          ))}
        </div>
      </div>
    </header>
  );
}
