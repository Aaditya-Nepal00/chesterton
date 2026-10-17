import type { ReactNode } from "react";
import workflowYaml from "../../../examples/chesterton.yml?raw";
import { Card, CopyButton, Ext, PageTitle, SectionHeading } from "../components/ui";
import { REPO_URL } from "../lib/firebase";

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="grid grid-cols-[32px_1fr] gap-x-3">
      <span className="mono flex h-8 w-8 items-center justify-center rounded-full border border-accent text-sm text-accent">
        {n}
      </span>
      <div className="min-w-0 pt-1">
        <h3 className="font-semibold text-ink">{title}</h3>
        <div className="mt-1 grid gap-2 text-ink-2">{children}</div>
      </div>
    </li>
  );
}

function Secret({ name, children }: { name: string; children: ReactNode }) {
  return (
    <li className="grid gap-x-3 sm:grid-cols-[260px_1fr]">
      <span className="mono text-sm text-ink">{name}</span>
      <span className="text-sm">{children}</span>
    </li>
  );
}

function Box({ title, sub, accent }: { title: string; sub: string; accent?: boolean }) {
  return (
    <div
      className={
        "rounded-[var(--radius)] border px-3 py-2 " +
        (accent ? "border-accent bg-accent-soft" : "border-line-strong bg-surface")
      }
    >
      <div className={"text-sm font-semibold " + (accent ? "text-accent" : "text-ink")}>{title}</div>
      <div className="text-xs text-ink-3">{sub}</div>
    </div>
  );
}

function Arrow() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" className="mx-auto shrink-0 rotate-90 text-ink-3 md:rotate-0">
      <path d="M4 12h15M14 7l5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Install() {
  return (
    <div className="grid gap-12">
      <div>
        <PageTitle eyebrow="Install">Add Chesterton to a repository</PageTitle>
        <p className="-mt-4 max-w-[720px] text-ink-2">
          Chesterton runs as a GitHub Action in your repo and posts as its own GitHub App. No server to run: state lives
          in a free Firestore database. About ten minutes.
        </p>
      </div>

      <ol className="grid max-w-[880px] gap-8">
        <Step n={1} title="Create the GitHub App">
          <p>
            In GitHub → Settings → Developer settings → GitHub Apps → New. Turn the webhook off (the Action receives
            events, not the App). Repository permissions: <strong className="text-ink">Contents</strong>,{" "}
            <strong className="text-ink">Issues</strong> and <strong className="text-ink">Pull requests</strong> read
            &amp; write; Metadata read. Generate a private key, note the App ID, and install the App on your repo.
          </p>
        </Step>
        <Step n={2} title="Add the secrets">
          <p>
            Repo → Settings → Secrets and variables → Actions. You also need a Google Cloud project with Firestore and a
            service account that has <span className="mono">roles/datastore.user</span>.
          </p>
          <ul className="grid gap-1.5 rounded-[var(--radius)] border border-line bg-surface px-4 py-3">
            <Secret name="GOOGLE_API_KEY">Gemini API key from Google AI Studio (free tier works)</Secret>
            <Secret name="GCP_SA_KEY">the service-account JSON key</Secret>
            <Secret name="CHESTERTON_APP_ID">the GitHub App id</Secret>
            <Secret name="CHESTERTON_APP_PRIVATE_KEY">the App's full .pem contents</Secret>
            <Secret name="GCP_PROJECT (variable)">your Google Cloud project id</Secret>
          </ul>
        </Step>
        <Step n={3} title="Add the workflow">
          <p>
            Save this as <span className="mono">.github/workflows/chesterton.yml</span>. It runs on merged PRs (ask why),
            on PR comments (record the answer) and on new PR commits (guard).
          </p>
          <Card className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-line px-4 py-2">
              <span className="mono text-xs text-ink-3">.github/workflows/chesterton.yml</span>
              <CopyButton text={workflowYaml} />
            </div>
            <pre className="max-h-[480px] overflow-auto px-4 py-3 text-[13px] leading-5 text-ink">
              <code>{workflowYaml}</code>
            </pre>
          </Card>
        </Step>
      </ol>

      <section aria-labelledby="arch" className="max-w-[1000px]">
        <SectionHeading id="arch">Architecture</SectionHeading>
        <p className="mb-6 max-w-[760px] text-ink-2">
          Every pull-request event starts a GitHub Actions job that runs a Google ADK 2 graph workflow. Gemini
          Flash-Lite screens the PR; Gemini Flash finds decisions, looks for a written reason and drafts questions; a
          deterministic verifier drops anything not backed by real evidence (quotes must appear verbatim, evidence ids
          must exist). Results and a step-by-step trace go to Firestore, which this dashboard reads live. Source on{" "}
          <Ext href={REPO_URL}>GitHub</Ext>.
        </p>
        <div className="grid items-center gap-3 md:grid-cols-[1fr_auto_2.2fr_auto_1fr_auto_1fr]" role="img" aria-label="GitHub Actions, then the ADK workflow (Flash-Lite screen, Flash judgment, deterministic verifier), then Firestore, then this dashboard">
          <Box title="GitHub Actions" sub="merge · comment · push" />
          <Arrow />
          <div className="rounded-[var(--radius)] border border-dashed border-accent px-3 py-3">
            <div className="mb-2 text-xs font-medium uppercase tracking-[0.08em] text-accent">ADK workflow</div>
            <div className="grid gap-2 sm:grid-cols-3">
              <Box title="Screen" sub="Gemini Flash-Lite" accent />
              <Box title="Judge" sub="Gemini Flash" accent />
              <Box title="Verify" sub="plain Python" />
            </div>
          </div>
          <Arrow />
          <Box title="Firestore" sub="runs · decisions · traces" />
          <Arrow />
          <Box title="This dashboard" sub="Firebase Hosting, live" />
        </div>
      </section>
    </div>
  );
}
