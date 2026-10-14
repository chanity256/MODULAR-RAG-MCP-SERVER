import type { ReactNode } from "react";

export default function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div className="mt-1 truncate text-2xl font-semibold text-slate-900 dark:text-slate-100">{value}</div>
      {hint && <div className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">{hint}</div>}
    </div>
  );
}
