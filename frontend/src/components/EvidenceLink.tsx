import { parseEvidence, useDiffAnchor } from "../lib/github";
import { Ext } from "./ui";

/** Links an evidence id. Uses the html_url stored with the analysis when there is one
 *  (Build 7a+); older docs fall back to a URL derived from the id. */
export function EvidenceLink({ id, prUrl, repo, url }: { id: string; prUrl: string; repo: string; url?: string }) {
  const ref = parseEvidence(id, prUrl, repo);
  const derivedDiff = useDiffAnchor(prUrl, !url && ref.kind === "diff" ? ref.path : null);
  const href = url ?? (ref.kind === "diff" ? derivedDiff : ref.href);
  return (
    <Ext href={href} className="mono text-xs">
      {ref.label}
    </Ext>
  );
}