import { useState, type ReactNode } from "react";
import type { StatusInfo, Tone } from "../lib/status";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const TONE_ICON: Record<Tone, ReactNode> = {
  ok: <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />,
  missing: <><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M8 5v3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /><circle cx="8" cy="11" r="0.9" fill="currentColor" /></>,
  partial: <><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor" /></>,
  slate: <circle cx="8" cy="8" r="3" fill="currentColor" />,
  muted: <path d="M4 8h8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />,
  fail: <path d="M4.5 4.5l7 7m0-7l-7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />,
  guard: <path d="M8 2.2l4.8 1.8v3.7c0 3-2.1 5.1-4.8 6.1-2.7-1-4.8-3.1-4.8-6.1V4z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />,
  accent: <circle cx="8" cy="8" r="3.2" fill="currentColor" className="pulse" />,
};

export function StatusIcon({ tone, className }: { tone: Tone; className?: string }) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" className={cx("shrink-0", className)}>
      {TONE_ICON[tone]}
    </svg>
  );
}

/** A status pill: icon + text label + tone. Never color alone. */
export function Pill({ info, size = "sm", title }: { info: StatusInfo; size?: "sm" | "xs"; title?: string }) {
  return (
    <span
      title={title ?? info.hint}
      className={cx(
        `tone-${info.tone}`,
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-medium",
        size === "sm" ? "px-2.5 py-0.5 text-sm" : "px-2 py-px text-xs",
      )}
    >
      <StatusIcon tone={info.tone} />
      {info.label}
    </span>
  );
}

export function KindChip({ kind }: { kind: string }) {
  const label = { analyze: "Analyze", record: "Record", guard: "Guard" }[kind] ?? kind;
  return (
    <span className="mono inline-block w-[68px] rounded border border-line bg-surface-2 px-1.5 py-px text-center text-xs uppercase tracking-wide text-ink-2">
      {label}
    </span>
  );
}

/** Marks whether a step is plain code or a Gemini call (with the model). */
export function EngineMarker({ model, gemini }: { model?: string | null; gemini: boolean }) {
  if (!gemini) {
    return (
      <span className="mono inline-flex items-center gap-1 text-xs text-ink-3" title="Deterministic Python">
        <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
          <path d="M5.5 4L2 8l3.5 4M10.5 4L14 8l-3.5 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        code
      </span>
    );
  }
  return (
    <span
      className="mono inline-flex items-center gap-1 rounded border border-accent/40 bg-accent-soft px-1.5 py-px text-xs text-accent"
      title="Gemini call"
    >
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
        <path d="M8 1.5c.6 3.2 3.3 5.9 6.5 6.5-3.2.6-5.9 3.3-6.5 6.5C7.4 11.3 4.7 8.6 1.5 8 4.7 7.4 7.4 4.7 8 1.5z" fill="currentColor" />
      </svg>
      {model ?? "Gemini"}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden="true" />;
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-[var(--radius)] border border-dashed border-line-strong bg-surface px-5 py-6">
      <p className="font-medium text-ink">{title}</p>
      <div className="mt-1 max-w-prose text-sm text-ink-2">{children}</div>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: string }) {
  return (
    <div role="alert" className="tone-fail rounded-[var(--radius)] border px-4 py-3 text-sm">
      <span className="font-medium">Couldn't load from Firestore.</span> <span className="mono text-xs">{error}</span>
    </div>
  );
}

export function Card({ children, className, as: As = "div" }: { children: ReactNode; className?: string; as?: "div" | "article" | "section" }) {
  return (
    <As className={cx("rounded-[var(--radius)] border border-line bg-surface shadow-[var(--shadow)]", className)}>
      {children}
    </As>
  );
}

export function SectionHeading({ children, aside, id }: { children: ReactNode; aside?: ReactNode; id?: string }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h2 id={id} className="text-sm font-semibold uppercase tracking-[0.08em] text-ink-2">
        {children}
      </h2>
      {aside && <div className="text-sm text-ink-3">{aside}</div>}
    </div>
  );
}

export function PageTitle({ children, eyebrow, aside }: { children: ReactNode; eyebrow?: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-sm text-ink-3">{eyebrow}</div>}
        <h1 className="break-words font-display text-2xl font-medium tracking-[-0.01em] text-ink sm:text-3xl">
          {children}
        </h1>
      </div>
      {aside}
    </div>
  );
}

export function Ext({ href, children, className }: { href?: string | null; children: ReactNode; className?: string }) {
  if (!href) return <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className={cx("inline-flex items-center gap-1", className)}>
      {children}
      <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" className="opacity-70">
        <path d="M4.5 2.5h5v5M9.5 2.5L3 9" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

/** Text with `backtick` spans rendered as inline code (questions use Markdown backticks). */
export function InlineMd({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 2 ? (
          <code key={i} className="rounded bg-surface-2 px-1 py-px text-[0.9em] text-ink">
            {p.slice(1, -1)}
          </code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

export function Code({ children }: { children: ReactNode }) {
  return <code className="rounded bg-surface-2 px-1 py-px text-[0.92em] text-ink">{children}</code>;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        ok = true;
      }
    } catch {
      ok = false;
    }
    if (!ok) {
      // Fallback for non-secure contexts and older browsers.
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        ok = document.execCommand("copy");
      } catch {
        ok = false;
      }
      document.body.removeChild(ta);
    }
    setState(ok ? "copied" : "failed");
    setTimeout(() => setState("idle"), 1800);
  }
  return (
    <button
      type="button"
      onClick={copy}
      className="rounded border border-line-strong bg-surface px-2.5 py-1 text-sm font-medium text-ink-2 hover:border-accent hover:text-accent"
    >
      <span aria-live="polite">{state === "copied" ? "Copied" : state === "failed" ? "Press Ctrl+C" : label}</span>
    </button>
  );
}

export function Stat({ label, value, sub, loading }: { label: string; value: ReactNode; sub?: ReactNode; loading?: boolean }) {
  return (
    <div className="min-w-0 border-l border-line pl-4">
      <div className="text-sm text-ink-3">{label}</div>
      {loading ? (
        <Skeleton className="mt-1.5 h-7 w-16" />
      ) : (
        <div className="mono mt-0.5 text-2xl font-medium tracking-tight text-ink tabular-nums">{value}</div>
      )}
      {sub && <div className="mt-0.5 text-xs text-ink-3">{sub}</div>}
    </div>
  );
}
