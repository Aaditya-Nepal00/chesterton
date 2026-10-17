import { useId, useState } from "react";
import { createPortal } from "react-dom";
import type { BackfillDoc, DecisionCounts, RationaleStatus } from "../lib/types";
import { cx } from "./ui";

/* Segment order is fixed (found → partial → missing, then self-evident): the bar fills
   were validated as adjacent pairs in this order for color-vision deficiency. */
const SEGMENTS: { key: RationaleStatus; label: string; bar: string; note?: string }[] = [
  { key: "found", label: "Reason found", bar: "bar-found" },
  { key: "partial", label: "Partial reason", bar: "bar-partial" },
  { key: "missing", label: "Missing why", bar: "bar-missing" },
  { key: "self_evident", label: "Self-evident", bar: "bar-self", note: "left out of the rate" },
];

/** Stacked bar of decision statuses, with a hover/focus tooltip per segment and a legend
    that carries every count (so identity and value never depend on color). */
export function DecisionBar({ counts }: { counts: DecisionCounts }) {
  const [active, setActive] = useState<RationaleStatus | null>(null);
  const total = SEGMENTS.reduce((n, s) => n + counts[s.key], 0);
  if (!total) return null;
  const shown = SEGMENTS.filter((s) => counts[s.key] > 0);
  const tip = shown.find((s) => s.key === active);
  const tipLeft = (() => {
    if (!tip) return 0;
    let before = 0;
    for (const s of shown) {
      if (s.key === tip.key) break;
      before += counts[s.key];
    }
    return ((before + counts[tip.key] / 2) / total) * 100;
  })();
  return (
    <figure className="m-0">
      <div className="relative pt-9">
        {tip && (
          <div
            role="tooltip"
            className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded border border-line-strong bg-surface px-2 py-1 text-xs text-ink shadow-[var(--shadow)]"
            style={{ left: `clamp(70px, ${tipLeft}%, calc(100% - 70px))` }}
          >
            <span className="font-medium">{tip.label}</span>{" "}
            <span className="mono text-ink-2">
              {counts[tip.key]} of all {total} decisions ({Math.round((100 * counts[tip.key]) / total)}%)
            </span>
          </div>
        )}
        <div className="flex h-4 w-full gap-[2px]" role="group" aria-label="Decisions by status">
          {shown.map((s, i) => (
            <div
              key={s.key}
              role="img"
              tabIndex={0}
              aria-label={`${s.label}: ${counts[s.key]} of ${total}`}
              onMouseEnter={() => setActive(s.key)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(s.key)}
              onBlur={() => setActive(null)}
              className={cx(
                s.bar,
                "h-full min-w-[6px] outline-offset-2",
                i === 0 && "rounded-l-[4px]",
                i === shown.length - 1 && "rounded-r-[4px]",
                active && active !== s.key && "opacity-60",
              )}
              style={{ flexGrow: counts[s.key], flexBasis: 0 }}
            />
          ))}
        </div>
      </div>
      <figcaption>
        <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-sm text-ink-2">
          {SEGMENTS.map((s) => (
            <li key={s.key} className="inline-flex items-center gap-1.5">
              <span className={cx(s.bar, "inline-block h-2.5 w-2.5 rounded-[2px]")} aria-hidden="true" />
              <span>{s.label}</span>
              <span className="mono font-medium text-ink">{counts[s.key]}</span>
              {s.note && <span className="text-xs text-ink-3">({s.note})</span>}
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}

/** "How we measured": the method text the backfill stored, in a native popover. */
export function MethodPopover({ b, label = "How we measured" }: { b: BackfillDoc; label?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <>
      <button
        type="button"
        popoverTarget={id}
        className="cursor-pointer text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
      >
        {label}
      </button>
      {/* Portaled: the trigger often sits inside a <p> or <span>, where a block can't go. */}
      {createPortal(
      <div id={id} popover="auto" className="method-popover">
        <div className="flex items-start justify-between gap-4">
          <h2 className="font-semibold text-ink">How we measured</h2>
          <button
            type="button"
            popoverTarget={id}
            popoverTargetAction="hide"
            className="rounded border border-line px-2 py-0.5 text-sm text-ink-2 hover:border-line-strong hover:text-ink"
          >
            Close
          </button>
        </div>
        <p className="mt-3 text-sm leading-6">{b.method || "No method recorded for this backfill."}</p>
        <p className="mt-3 border-t border-line pt-3 text-xs text-ink-3">
          {b.repo}: {b.considered} of {b.sample_size} PRs in the sample processed · {b.calls_used} Gemini calls
          {b.models_used.length ? ` (${b.models_used.join(", ")})` : ""}. Numbers describe this sample only.
        </p>
      </div>,
      document.body,
      )}
    </>
  );
}
