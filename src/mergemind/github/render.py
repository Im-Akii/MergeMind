"""Markdown rendering for bot comments. Every comment carries a hidden idempotency marker."""

from __future__ import annotations

import re

from mergemind.models import IssueState, Phase, TriageReport

CARD_MARKER = "<!-- mergemind:card -->"

_MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9-]+)")


def marker(idem_key: str) -> str:
    return f"<!-- mergemind:{idem_key} -->"


def escape_mentions(text: str) -> str:
    """Turn @user into `user` so LLM-written text can never ping anyone."""
    return _MENTION.sub(lambda m: f"`{m.group(1)}`", text or "")


PHASE_LINE = {
    Phase.TRIAGING: "Investigating the code for this issue…",
    Phase.NEEDS_INFO: "Needs more information before it can be worked on.",
    Phase.DUPLICATE_SUGGESTED: "Looks like a duplicate; a maintainer can reply `/mergemind not-duplicate`.",
    Phase.QUESTION: "Looks like a question; answered from the docs below.",
    Phase.OUT_OF_SCOPE_SUGGESTED: "Possibly out of scope; a maintainer will decide.",
    Phase.FEATURE_REVIEW: "Feature request: waiting for a maintainer (`/mergemind accept`).",
    Phase.SECURITY_ESCALATED: "Sent to the maintainers privately. Please see SECURITY.md.",
    Phase.ESCALATED: "Handed to a maintainer.",
    Phase.ACCEPTED: "Accepted as a fixable issue.",
    Phase.AWAITING_APPROVAL: "Accepted; waiting for a maintainer to approve contributor access.",
    Phase.ACCESS_OFFERED: "Open for the issue author to fix: reply `/contribute` to get access.",
    Phase.ACCEPTED_UNCLAIMED: "Accepted; nobody is working on it right now.",
    Phase.ACCESS_GRANTED: "Access invitation sent to the contributor.",
    Phase.WORKING: "The contributor is working on a fix.",
    Phase.PR_ANALYSING: "Analysing the pull request…",
    Phase.CHANGES_REQUESTED: "Changes requested on the pull request.",
    Phase.SAFETY_HOLD: "The pull request is on hold for maintainer review.",
    Phase.READY_FOR_MAINTAINER: "Verification passed. A maintainer can merge with `/mergemind approve`.",
    Phase.MERGING: "Re-verifying against the latest base and merging…",
    Phase.POST_MERGE_WATCH: "Merged. Watching the default branch CI.",
    Phase.REVERT_PROPOSED: "Default-branch CI failed after merge; a revert PR was opened.",
    Phase.DONE: "Done. Merged and verified. Thank you!",
    Phase.CLOSED: "Closed.",
    Phase.PERMISSION_BLOCKED: "Blocked: MergeMind lacks a GitHub permission it needs. A maintainer has been notified.",
}


def render_card(state: IssueState, report: TriageReport | None = None) -> str:
    lines = [f"### MergeMind · {state.phase.value.replace('_', ' ').lower()}"]
    lines.append(PHASE_LINE.get(state.phase, ""))
    if report:
        lines.append("")
        lines.append(f"**{report.issue_type}** · {escape_mentions(report.summary)}")
        if report.localization:
            top = report.localization[0]
            sym = f" `{top.symbol}()`" if top.symbol else ""
            lines.append(f"**Likely location:** `{top.file}` L{top.start}–{top.end}{sym}")
        if report.missing_info:
            lines.append("**Missing:** " + "; ".join(escape_mentions(m.field) for m in report.missing_info))
        if report.acceptance_criteria:
            lines.append("**Done when:**")
            lines.extend(f"- {escape_mentions(c)}" for c in report.acceptance_criteria)
    lines.append("")
    lines.append("<sub>`/contribute` · `/mergemind why` · maintainers: `/mergemind approve | accept | revoke | stop`</sub>")
    lines.append(CARD_MARKER)
    return "\n".join(lines)
