/* GitHub URLs derived from what Firestore stores. */
import { useEffect, useState } from "react";

const cache = new Map<string, string>();

async function sha256Hex(text: string): Promise<string> {
  const hit = cache.get(text);
  if (hit) return hit;
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  const hex = [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  cache.set(text, hex);
  return hex;
}

/** PR "Files changed" anchor for a path (GitHub uses sha256(path)). */
export function useDiffAnchor(prUrl: string, path: string | null | undefined): string {
  const [href, setHref] = useState(`${prUrl}/files`);
  useEffect(() => {
    if (!path || !crypto?.subtle) return;
    let alive = true;
    sha256Hex(path).then((h) => alive && setHref(`${prUrl}/files#diff-${h}`));
    return () => {
      alive = false;
    };
  }, [prUrl, path]);
  return href;
}

export interface EvidenceRef {
  kind: "diff" | "pr" | "commit" | "comment" | "issue" | "other";
  label: string;
  path?: string;
  href: string; // best-effort; "diff" is resolved by useDiffAnchor
}

/** Evidence ids: pr:body, commit:<sha7>, comment:<id>, issue:<n>, diff:<path>#<n>. */
export function parseEvidence(id: string, prUrl: string, repo: string): EvidenceRef {
  if (id === "pr:body") return { kind: "pr", label: "PR description", href: prUrl };
  if (id.startsWith("commit:")) {
    const sha = id.slice(7);
    return { kind: "commit", label: `commit ${sha}`, href: `${prUrl}/commits/${sha}` };
  }
  if (id.startsWith("comment:")) {
    const cid = id.slice(8);
    // Issue comments use #issuecomment-<id>; review comments use #discussion_r<id>. The
    // analysis doc doesn't store which, so this anchor is right for conversation comments.
    return { kind: "comment", label: `comment ${cid}`, href: `${prUrl}#issuecomment-${cid}` };
  }
  if (id.startsWith("issue:")) {
    const n = id.slice(6);
    return { kind: "issue", label: `issue #${n}`, href: `https://github.com/${repo}/issues/${n}` };
  }
  if (id.startsWith("diff:")) {
    const rest = id.slice(5);
    const hash = rest.lastIndexOf("#");
    const path = hash > 0 ? rest.slice(0, hash) : rest;
    const hunk = hash > 0 ? rest.slice(hash + 1) : "0";
    return { kind: "diff", label: `${path} · hunk ${Number(hunk) + 1}`, path, href: `${prUrl}/files` };
  }
  return { kind: "other", label: id, href: prUrl };
}

export function recordFileUrl(repo: string, filePath: string): string {
  // HEAD resolves to the repo's default branch.
  return `https://github.com/${repo}/blob/HEAD/${filePath}`;
}
