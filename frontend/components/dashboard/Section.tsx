import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{title}</h1>
        {description && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title}</h2>
      {children}
    </section>
  );
}

export function Badge({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "slate" | "blue" | "green" | "amber" | "purple";
}) {
  const tones = {
    slate: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
    blue: "bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
    green: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300",
    amber: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
    purple: "bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
  };
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function SourceBadge({ source }: { source: string }) {
  const tone = source === "chat" ? "purple" : source === "agentic" ? "amber" : "blue";
  return <Badge tone={tone}>{source}</Badge>;
}

export function RefreshControls({
  autoRefresh,
  onToggle,
  onRefresh,
}: {
  autoRefresh: boolean;
  onToggle: (value: boolean) => void;
  onRefresh: () => void;
}) {
  return (
    <>
      <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={autoRefresh} onChange={(e) => onToggle(e.target.checked)} />
        Auto-refresh (10s)
      </label>
      <button
        type="button"
        onClick={onRefresh}
        className="rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-700"
      >
        Refresh
      </button>
    </>
  );
}
