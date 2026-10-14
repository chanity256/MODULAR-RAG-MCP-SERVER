export default function ComingSoon({ title, task }: { title: string; task: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{title}</h1>
      <p className="text-sm text-slate-500 dark:text-slate-400">Coming soon ({task}).</p>
    </div>
  );
}
