"""The starter briefing posted with an access grant. The skeleton is code; only the summary,
criteria and hints come from the (verified) triage report. It always passes the leak guard."""

from __future__ import annotations

from datetime import datetime, timezone

from mergemind.code.graph import is_test
from mergemind.github.render import escape_mentions
from mergemind.models import TriageReport


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_briefing(
    *,
    repo: str,
    number: int,
    login: str,
    report: TriageReport,
    sha: str | None,
    mode: str,
    expires_at: float,
    test_commands: list[str] | None = None,
    default_branch: str = "main",
) -> str:
    ref = sha or default_branch
    branch = f"mergemind/issue-{number}"
    lines = [f"### MergeMind · starter briefing for @{login}", ""]
    if mode == "collaborator":
        lines.append(f"You've been invited as a collaborator with **push** access so you can fix this issue. "
                     f"Accept the invitation at https://github.com/{repo}/invitations — access ends on "
                     f"**{_when(expires_at)}** or when the fix is merged.")
    elif mode == "existing":
        lines.append("You already have access to this repository. MergeMind will track this fix until it is merged.")
    else:
        lines.append(f"Fork the repository and open a pull request from your fork. "
                     f"MergeMind tracks this fix until **{_when(expires_at)}**.")
    lines += ["", "**Problem**", escape_mentions(report.summary), ""]

    if report.localization:
        lines.append("**Where to start**")
        for r in report.localization[:3]:
            sym = f" `{r.symbol}`" if r.symbol else ""
            role = r.role.replace("_", " ")
            lines.append(f"- [`{r.file}` L{r.start}–{r.end}](https://github.com/{repo}/blob/{ref}/{r.file}#L{r.start}-L{r.end})"
                         f"{sym} — {role}")
        lines.append("")

    loc_files = {r.file for r in report.localization}
    related = sorted({s.file for s in report.scope if s.file not in loc_files and not is_test(s.file)})
    tests = sorted({s.file for s in report.scope if is_test(s.file)})
    if related:
        lines += ["**What else it touches**", ", ".join(f"`{f}`" for f in related[:10]), ""]
    if tests or test_commands:
        lines.append("**Tests to run**")
        for cmd in (test_commands or [])[:2]:
            target = f" {' '.join(tests[:3])}" if tests and cmd in ("pytest",) else ""
            lines.append(f"- `{cmd}{target}`")
        if tests and not test_commands:
            lines.append("- " + ", ".join(f"`{t}`" for t in tests[:6]))
        lines.append("")

    if report.acceptance_criteria:
        lines.append("**Done when**")
        lines += [f"- {escape_mentions(c)}" for c in report.acceptance_criteria]
        lines.append("")
    if report.regression_test_hint:
        lines += ["**Regression test**", "Add a test that fails before your fix and passes after it: "
                  + escape_mentions(report.regression_test_hint), ""]
    if report.open_questions:
        lines.append("**Open questions**")
        lines += [f"- {escape_mentions(q)}" for q in report.open_questions[:5]]
        lines.append("")

    scope = sorted({s.file for s in report.scope})
    lines += [
        "**How this works**",
        f"- Work on a branch named `{branch}` and open a pull request whose description says `Fixes #{number}`.",
        "- Keep changes to the files above where you can; anything else is flagged for a maintainer.",
        "- MergeMind checks the PR (tests, scope, safety) and a maintainer approves the merge.",
        "- Reply `/mergemind withdraw` if you can't continue, and access is removed.",
    ]
    if scope:
        lines += ["", f"<details><summary>Files in scope ({len(scope)})</summary>\n\n"
                  + "\n".join(f"- `{f}`" for f in scope[:40]) + "\n</details>"]
    return "\n".join(lines)
