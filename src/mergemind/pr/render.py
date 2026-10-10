"""PR comment rendering. The public comment carries what the contributor needs to act on;
safety details go to the maintainers only."""

from __future__ import annotations

from mergemind.github.render import escape_mentions
from mergemind.pr.analyse import PRVerdict

PR_CARD = "<!-- mergemind:pr-card -->"

TITLE = {
    "ready_for_maintainer": "✅ Verification passed",
    "changes_requested": "🔧 Changes requested",
    "safety_hold": "⏸ On hold for maintainer review",
    "escalate": "⏸ On hold for maintainer review",
}


def render_pr_comment(v: PRVerdict, issue_number: int, contributor: str | None) -> str:
    who = f"@{contributor} " if contributor else ""
    lines = [f"### MergeMind · {TITLE[v.decision]}", f"Checked `{v.head_sha[:10]}` for #{issue_number}.", ""]

    if v.decision == "safety_hold":
        lines.append(f"{who}thanks! This change needs a maintainer's review before MergeMind can continue. "
                     "No action is needed from you right now.")
    elif v.decision == "changes_requested":
        lines.append(f"{who}thanks for the fix! A few things to address:")
        lines.append("")
        if v.conflicts or not v.mergeable:
            files = ", ".join(f"`{f}`" for f in v.conflicts[:8]) or "see the PR page"
            lines.append(f"- **Merge conflicts** with the base branch: {files}. Please rebase or merge the base branch.")
        if v.tests.tests not in (None, "success"):
            lines.append("- **Tests are failing**" + (":" if v.tests.failing else "."))
            lines += [f"  - `{t}`" for t in v.tests.failing[:10]]
        if v.tests.regression_required and v.tests.regression != "success" and v.tests.tests == "success":
            if v.tests.regression in (None, "skipped"):
                lines.append("- **Add a regression test**: a test that fails without your fix and passes with it.")
            else:
                lines.append("- **Regression test**: the new test(s) should fail on the base branch and pass with "
                             "your fix; they currently don't.")
        for c in v.criteria:
            if c.met == "no":
                lines.append(f"- **Not yet met:** {escape_mentions(c.criterion)} — {escape_mentions(c.explanation)}")
        for p in v.issues_found[:5]:
            lines.append(f"- **Problem:** {escape_mentions(p.text)}")
        if v.out_of_scope_files and "changes outside the issue's scope" in v.reasons:
            lines.append("- **Out of scope:** please limit changes to the issue's files: "
                         + ", ".join(f"`{f}`" for f in v.out_of_scope_files[:8]))
        lines += ["", "Push to this branch and MergeMind will check again."]
    else:
        lines.append(f"{who}thanks! Everything MergeMind checks has passed:")
        lines.append("")
        lines.append("- Tests pass" + (" and the regression test fails without the fix" if v.tests.regression == "success" else ""))
        lines.append("- No merge conflicts")
        for c in v.criteria:
            lines.append(f"- {escape_mentions(c.criterion)}")
        if v.out_of_scope_files:
            lines.append(f"- Note for maintainers: also changes {', '.join(f'`{f}`' for f in v.out_of_scope_files[:6])}")
        lines += ["", "A maintainer can merge with `/mergemind approve`."]

    lines.append(PR_CARD)
    return "\n".join(lines)


def render_private_details(v: PRVerdict) -> str:
    lines = [f"PR #{v.pr_number} @ {v.head_sha[:10]} decision={v.decision}", ""]
    if v.reasons:
        lines += ["Reasons:"] + [f"- {r}" for r in v.reasons] + [""]
    if v.safety_flags:
        lines.append("Safety flags:")
        for f in v.safety_flags:
            loc = f"{f['file']}" + (f":{f['line']}" if f.get("line") else "")
            lines.append(f"- [{f['kind']}] {loc}: {f['detail']}")
        lines.append("")
    if v.out_of_scope_files:
        lines.append("Out of scope: " + ", ".join(v.out_of_scope_files))
    if v.affected:
        lines.append("Affected callers: " + ", ".join(v.affected[:15]))
    for c in v.criteria:
        lines.append(f"- criterion [{c.met}] {c.criterion}: {c.explanation}")
    return "\n".join(lines)
