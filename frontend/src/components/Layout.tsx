import { useEffect, useState } from "react";
import { NavLink, Outlet, ScrollRestoration } from "react-router";
import { firebaseConfigured, REPO_URL } from "../lib/firebase";
import { cx, Ext } from "./ui";

type Theme = "system" | "light" | "dark";
const KEY = "chesterton-theme";

function readTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      /* storage blocked: theme still applies for this visit */
    }
  }, [theme]);
  const next: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
  const label = { system: "Theme: system", light: "Theme: light", dark: "Theme: dark" }[theme];
  return (
    <button
      type="button"
      onClick={() => setTheme(next[theme])}
      className="inline-flex h-8 items-center gap-1.5 rounded border border-line bg-surface px-2 text-sm text-ink-2 hover:border-line-strong hover:text-ink"
      aria-label={`${label}. Click to change.`}
      title={label}
    >
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        {theme === "dark" ? (
          <path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z" fill="currentColor" />
        ) : theme === "light" ? (
          <>
            <circle cx="8" cy="8" r="3" fill="currentColor" />
            <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </>
        ) : (
          <>
            <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor" />
          </>
        )}
      </svg>
      <span className="hidden sm:inline">{theme[0].toUpperCase() + theme.slice(1)}</span>
    </button>
  );
}

const NAV = [
  { to: "/", label: "Overview", end: true, wide: true },
  { to: "/decisions", label: "Decisions", end: false, wide: false },
  { to: "/backfill", label: "Backfill", end: false, wide: false },
  { to: "/install", label: "Install", end: false, wide: false },
];

export function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-2 focus:z-10 focus:rounded focus:bg-surface focus:px-3 focus:py-1"
      >
        Skip to content
      </a>
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-[1200px] items-center gap-2 px-4 py-3 sm:gap-4 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2 text-ink no-underline hover:text-ink" aria-label="Chesterton, overview">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <rect x="3" y="3" width="18" height="18" rx="3" fill="none" stroke="var(--accent)" strokeWidth="1.8" />
              <path d="M8 8h8M8 12h5M8 16h8" stroke="var(--ink)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
            <span className="font-display text-xl font-semibold tracking-[-0.01em]">Chesterton</span>
          </NavLink>
          <nav aria-label="Main" className="ml-auto flex items-center gap-1 sm:ml-6 sm:mr-auto">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  cx(
                    "rounded px-2 py-1 text-sm font-medium no-underline sm:px-2.5",
                    n.wide && "hidden sm:inline-block",
                    isActive ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                  )
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="hidden items-center gap-3 sm:flex">
            <Ext href={REPO_URL} className="text-sm text-ink-2 no-underline hover:text-ink">
              GitHub
            </Ext>
          </div>
          <ThemeToggle />
        </div>
      </header>
      {!firebaseConfigured && (
        <div className="tone-missing border-b px-4 py-2 text-center text-sm">
          Firestore isn't configured for this build (missing <span className="mono">VITE_FIREBASE_*</span>), so no data
          can load.
        </div>
      )}
      <main id="main" className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 sm:px-6 sm:py-10">
        <Outlet />
      </main>
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-6 gap-y-1 px-4 py-4 text-xs text-ink-3 sm:px-6">
          <span>Live from Firestore · read-only</span>
          <span>GitHub Actions · Google ADK · Gemini · Firebase</span>
          <Ext href={REPO_URL} className="ml-auto text-ink-3">
            Source
          </Ext>
        </div>
      </footer>
      <ScrollRestoration />
    </div>
  );
}
