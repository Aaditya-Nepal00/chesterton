import { shortModel } from "../lib/format";
import { cx } from "./ui";

const CHIP = "inline-flex h-5 items-center whitespace-nowrap rounded border px-1.5 text-xs";

/** Short model chips ("3.5 Flash-Lite"); the full ids are in the tooltip and for screen readers.
    With `max`, extra models collapse into a "+N" chip; `wrap` lets chips (never names) wrap. */
export function ModelChips({ models, max, wrap = false }: { models: string[]; max?: number; wrap?: boolean }) {
  const shown = max ? models.slice(0, max) : models;
  const rest = max ? models.slice(max) : [];
  return (
    <span className={cx("flex min-w-0 items-center gap-1", wrap && "flex-wrap")} title={`Answered by ${models.join(", ")}`}>
      <span className="sr-only">Answered by {models.join(", ")}</span>
      {shown.map((m) => (
        <span key={m} aria-hidden="true" className={`${CHIP} border-line bg-surface-2 text-ink-2`}>
          {shortModel(m)}
        </span>
      ))}
      {rest.length > 0 && (
        <span aria-hidden="true" title={rest.join(", ")} className={`${CHIP} mono border-dashed border-line-strong text-ink-3`}>
          +{rest.length}
        </span>
      )}
    </span>
  );
}
