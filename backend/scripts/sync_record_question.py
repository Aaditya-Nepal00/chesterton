"""Open a PR that updates a decision record file's "Question asked:" line to match the
question text stored in Firestore (e.g. after rerender_questions.py). App token only.

    uv run python scripts/sync_record_question.py --repo owner/demo --number 1          # dry run
    uv run python scripts/sync_record_question.py --repo owner/demo --number 1 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import re
import sys

from google.cloud import firestore

from app.config import get_settings
from app.github.write import GitHubWriter
from app.store.firestore import decision_doc_id

QUESTION_LINE = re.compile(r"^- Question asked: .*$", re.MULTILINE)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--number", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    db = firestore.Client(project=s.google_cloud_project, database=s.firestore_database)
    snap = db.collection("decisions").document(decision_doc_id(args.repo, args.number)).get()
    if not snap.exists:
        print("no such decision record")
        return 1
    rec = snap.to_dict()
    async with await GitHubWriter.for_repo(args.repo) as gh:
        default = await gh.get_default_branch()
        current = await gh.get_file(rec["file_path"], ref=default)
        if current is None:
            print(f"{rec['file_path']} is not on {default} yet (record PR not merged?)")
            return 1
        new_line = f"- Question asked: {rec['question']}"
        if not QUESTION_LINE.search(current["content"]):
            print("no 'Question asked:' line found")
            return 1
        updated = QUESTION_LINE.sub(lambda _m: new_line, current["content"], count=1)
        diff = list(
            difflib.unified_diff(
                current["content"].splitlines(),
                updated.splitlines(),
                rec["file_path"],
                rec["file_path"],
                lineterm="",
                n=0,
            )
        )
        print("\n".join(diff) or "(already up to date)")
        if not diff or not args.apply:
            print("\n(dry run)" if diff else "")
            return 0
        branch = f"chesterton/record-{rec['id']}-question"
        await gh.create_branch(branch)
        await gh.put_file(
            branch,
            rec["file_path"],
            updated,
            f"Decision record {rec['id']}: update the question wording",
        )
        subject = rec["symbols"][0] if rec.get("symbols") else "the value"
        pr = await gh.open_pr(
            branch,
            f"Decision record {rec['id']}: update the question wording",
            f"The question in `{rec['file_path']}` compared `{subject}` to a default that "
            "isn't in the diff. This updates the line to the question as it now reads on "
            f"#{rec['source_pr']}. The decision, the reason and the quote are unchanged.",
        )
        print(f"opened {pr['html_url']}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
