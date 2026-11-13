import { BUTTON_SECONDARY } from "@/components/shared/styles";
import { pageInfo } from "@/lib/data";

export default function Pagination({
  total,
  limit,
  offset,
  onOffsetChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
}) {
  const info = pageInfo(total, limit, offset);
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-3 text-xs text-slate-500 dark:text-slate-400">
      <span>
        {info.from}-{info.to} of {total}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={BUTTON_SECONDARY}
          disabled={!info.hasPrev}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          Previous
        </button>
        <span>
          Page {info.page} / {info.pages}
        </span>
        <button
          type="button"
          className={BUTTON_SECONDARY}
          disabled={!info.hasNext}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next
        </button>
      </div>
    </nav>
  );
}
