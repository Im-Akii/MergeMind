"""Effect handlers: the only place the engine touches GitHub or the LLM.

Every handler is idempotent. Handlers may return follow-up events (e.g. triage_done),
which the loop appends to the event log after the state commit.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from mergemind.config import Policy, Settings
from mergemind.github.client import GitHub, GitHubError
from mergemind.github.render import CARD_MARKER, escape_mentions, marker, render_card
from mergemind.llm import LLM, LLMError
from mergemind.models import Effect, Event, IssueState, Phase, TriageReport
from mergemind.store.db import Store
from mergemind.tracing import span

log = logging.getLogger(__name__)

# effects that write to GitHub; skipped entirely in `observe`
WRITE_EFFECTS = {
    "update_card", "post_comment", "notify_maintainers", "offer_access", "grant_access",
    "revoke_access", "post_briefing", "post_pr_review", "merge_pr", "open_revert_pr",
    "check_post_merge_ci",
}


@dataclass
class Context:
    settings: Settings
    store: Store
    gh: GitHub
    llm: LLM | None
    policies: dict[str, Policy] = field(default_factory=dict)
    dry_run: bool = False
    guards: dict[str, Any] = field(default_factory=dict)
    cache: Any = None  # RepoCache; tests inject a fake

    def policy(self, repo: str) -> Policy:
        if repo not in self.policies:
            text = None
            try:
                text = self.gh.get_file(repo, ".github/mergemind.yml")
            except GitHubError as e:
                log.warning("policy fetch failed for %s: %s", repo, e)
            self.policies[repo] = Policy.from_yaml(text)
        return self.policies[repo]

    def autonomy(self, repo: str) -> str:
        return self.policy(repo).effective_autonomy(self.settings)

    def guard(self, repo: str) -> "LeakGuard":
        """Leak guard for a repo; uses the local clone when there is one."""
        from mergemind.code.repo_cache import RepoCache, git
        from mergemind.guard.leak import LeakGuard

        if repo not in self.guards:
            path = RepoCache(self.settings.work_dir).path(repo)
            files: list[str] = []
            if (path / ".git").exists():
                files = [f for f in git(path, "ls-files", check=False).splitlines() if f]
            self.guards[repo] = LeakGuard.build(path if files else None, files,
                                                self.policy(repo).guard.sensitive_paths, self.llm, self.store)
        return self.guards[repo]


Handler = Callable[[Context, IssueState, Effect], list[Event]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def run_effects(ctx: Context, state: IssueState, effects: list[Effect]) -> list[Event]:
    follow_ups: list[Event] = []
    observe = ctx.autonomy(state.repo) == "observe" or state.data.get("stopped")
    for eff in effects:
        if eff.kind in WRITE_EFFECTS and (observe or ctx.dry_run):
            log.info("[%s#%d] skip write %s (observe/dry-run) %s", state.repo, state.number, eff.kind, eff.payload)
            continue
        fn = HANDLERS.get(eff.kind)
        if fn is None:
            log.warning("[%s#%d] effect %s not implemented yet", state.repo, state.number, eff.kind)
            continue
        key = eff.key(state)
        if eff.kind in WRITE_EFFECTS and ctx.store.action_status(key) == "done":
            continue
        try:
            follow_ups.extend(fn(ctx, state, eff))
            if eff.kind in WRITE_EFFECTS:
                ctx.store.record_action(key, state.repo, state.number, eff.kind, eff.payload, "done")
        except GitHubError as e:
            ctx.store.record_action(key, state.repo, state.number, eff.kind, eff.payload, "failed", e.error_class)
            follow_ups.extend(_on_github_error(ctx, state, eff, e))
    return follow_ups


def _on_github_error(ctx: Context, state: IssueState, eff: Effect, e: GitHubError) -> list[Event]:
    log.warning("[%s#%d] %s failed: %s", state.repo, state.number, eff.kind, e)
    from mergemind import tracing

    tracing.attrs(failed_effect=eff.kind, error_class=e.error_class, http_status=e.status,
                  recovery={"permission": "PERMISSION_BLOCKED", "not_found": "CLOSED"}.get(e.error_class, "retry_timer"))
    tracing.log("effect {effect} failed with {error_class}; recovering", effect=eff.kind, error_class=e.error_class)
    if e.error_class == "permission":
        return [_internal(state, "write_forbidden", {"effect": eff.kind, "status": e.status})]
    if e.error_class == "not_found":
        return [_internal(state, "issue_gone", {})]
    if e.error_class in ("rate_limited", "server", "network"):
        due = e.reset_at or time.time() + 60
        ctx.store.add_timer(due, "retry_effect", {"repo": state.repo, "number": state.number,
                                                 "effect": eff.model_dump()})
    return []


def _internal(state: IssueState, kind: str, payload: dict[str, Any]) -> Event:
    return Event(repo=state.repo, number=state.number, source="internal", kind=kind,
                 external_id=f"internal:{kind}:{uuid.uuid4().hex}", payload=payload)


# ----------------------------------------------------------------------- handlers


@handler("run_triage")
@span("WORKFLOW", "issue_run")
def run_triage(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.code.repo_cache import GitError
    from mergemind.triage import triage_issue

    if ctx.llm is None:
        return [_internal(state, "triage_failed", {"reason": "no_llm"})]
    try:
        out = triage_issue(settings=ctx.settings, store=ctx.store, gh=ctx.gh, llm=ctx.llm,
                           policy=ctx.policy(state.repo), repo=state.repo, number=state.number, cache=ctx.cache,
                           eval_run_id=ctx.settings.eval_run_id)
    except (LLMError, GitError) as e:
        reason = getattr(e, "error_class", type(e).__name__)
        log.warning("[%s#%d] triage failed: %s", state.repo, state.number, e)
        return [_internal(state, "triage_failed", {"reason": reason})]
    return [_internal(state, "triage_done", {
        "disposition": out.report.disposition, "run_id": out.run_id, "difficulty": out.difficulty,
        "scope_files": sorted({r.file for r in out.report.scope}),
    })]


def _report(ctx: Context, state: IssueState) -> TriageReport | None:
    run_id = state.data.get("report_run_id")
    if not run_id:
        return None
    row = ctx.store.conn.execute("SELECT report_json FROM runs WHERE id=?", (run_id,)).fetchone()
    return TriageReport.model_validate_json(row["report_json"]) if row and row["report_json"] else None


def _llm_written(ctx: Context, state: IssueState) -> bool:
    """False when the report came from a no-LLM fast gate (nothing model-written to critique)."""
    run_id = state.data.get("report_run_id")
    row = ctx.store.conn.execute("SELECT prompt_version FROM runs WHERE id=?", (run_id,)).fetchone() if run_id else None
    return bool(row) and row["prompt_version"] != "gate"


HELD_NOTE ="MergeMind prepared a message here that is waiting for a maintainer's review."


def _guarded(ctx: Context, state: IssueState, text: str, kind: str, *, use_critic: bool,
             allowed_mentions: set[str] | None = None) -> str | None:
    """Run outbound text through the leak guard. Returns postable text, or None if held
    (the held text goes to the maintainers privately)."""
    report = _report(ctx, state)
    scope = set(state.data.get("scope_files") or [])
    res = ctx.guard(state.repo).check(
        text, kind=kind, allowed_files=scope or None, allowed_mentions=allowed_mentions,
        context=f"Issue #{state.number}: {report.summary if report else state.title}",
        use_critic=use_critic, run_id=state.data.get("report_run_id"))
    if res.postable:
        return res.text
    log.info("[%s#%d] %s held by leak guard: %s", state.repo, state.number, kind,
             [f.get("type") for f in res.findings])
    _private_note(ctx, state, f"held {kind}", f"Held by the leak guard ({[f.get('type') for f in res.findings]}). "
                  f"Redacted draft:\n\n{res.text}")
    return None


def _private_note(ctx: Context, state: IssueState, title: str, body: str) -> None:
    channel = ctx.policy(state.repo).guard.maintainer_channel
    if not channel:
        log.warning("[%s#%d] %s: no maintainer_channel configured; kept in the local log only",
                    state.repo, state.number, title)
        return
    ctx.gh.request("POST", f"/repos/{channel}/issues", point="gh.notify", json_body={
        "title": f"[mergemind] {state.repo}#{state.number}: {title}",
        "body": f"Issue: https://github.com/{state.repo}/issues/{state.number}\n\n{body}"})


@handler("update_card")
def update_card(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    # security reports: say nothing about the content in public
    report = None if state.phase == Phase.SECURITY_ESCALATED else _report(ctx, state)
    body = render_card(state, report)
    guarded = _guarded(ctx, state, body, "card", use_critic=report is not None and _llm_written(ctx, state))
    body = guarded if guarded is not None else render_card(state, None).replace(
        CARD_MARKER, f"_{HELD_NOTE}_\n{CARD_MARKER}")
    rid = ctx.store.repo_id(state.repo)
    row = ctx.store.conn.execute(
        "SELECT card_comment_id FROM issues WHERE repo_id=? AND number=?", (rid, state.number)
    ).fetchone()
    card_id = row["card_comment_id"] if row else None
    if card_id is None:  # crash recovery: find an existing card by marker
        for c in ctx.gh.list_issue_comments(state.repo, state.number):
            if CARD_MARKER in (c.get("body") or ""):
                card_id = c["id"]
                break
    if card_id:
        ctx.gh.update_comment(state.repo, card_id, body)
    else:
        card_id = ctx.gh.create_comment(state.repo, state.number, body)["id"]
    ctx.store.conn.execute(
        "UPDATE issues SET card_comment_id=? WHERE repo_id=? AND number=?", (card_id, rid, state.number)
    )
    return []


TEMPLATES = {
    "denied": "{to_line}MergeMind can't do that: {reason}.",
    "access_denied": "{to_line}MergeMind couldn't grant access: {reason}.",
    "offer": "This issue is accepted and ready to fix. {author_line}, would you like to fix it yourself? "
             "Reply `/contribute` and MergeMind will give you access and a starter briefing.",
    "access_ended": "Access for this issue has ended ({reason}). Thanks for your time!",
    "merged": "Merged after verification, approved by `{by}`. MergeMind is now watching the default branch's CI.",
    "approval_voided": "The approval no longer applies: {reason}. MergeMind is checking again; "
                       "a maintainer will need to approve once more.",
    "merge_blocked": "MergeMind couldn't merge: {reason}. A maintainer needs to take a look.",
    "checkin": "Checking in: is the fix still in progress? Access is time-limited; reply here or open a PR to keep it.",
    "why": "MergeMind's reasoning for the current status is in the card above.",
    "maintainer_changes": "A maintainer requested changes:\n\n{text}",
}


@handler("post_comment")
def post_comment(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    p = dict(eff.payload)
    to = p.get("to")
    p["to_line"] = f"`{to}`: " if to else ""
    text = escape_mentions(TEMPLATES[p["template"]].format_map(_Default(p)))
    # templates are code-written; a maintainer's free text may still carry secrets
    text = _guarded(ctx, state, text, f"comment:{p['template']}", use_critic=False) or HELD_NOTE
    _post_once(ctx, state, eff, text)
    return []


def _post_once(ctx: Context, state: IssueState, eff: Effect, text: str) -> None:
    key = eff.key(state)
    if not _marker_exists(ctx, state, key):
        ctx.gh.create_comment(state.repo, state.number, f"{text}\n{marker(key)}")


# ------------------------------------------------------------------------ access


@handler("offer_access")
def offer_access(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    login = eff.payload.get("login") or state.author
    text = TEMPLATES["offer"].format(author_line=f"@{login}")
    text = _guarded(ctx, state, text, "offer", use_critic=False, allowed_mentions={login}) or HELD_NOTE
    _post_once(ctx, state, eff, text)
    return []


@handler("grant_access")
def grant_access(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.github import access

    policy = ctx.policy(state.repo)
    login = eff.payload["login"]
    leases = access.Leases(ctx.store)
    ttl = policy.scaled(policy.access.ttl)

    existing = leases.current(state.repo, state.number)
    if existing and existing["login"].lower() == login.lower():  # retried effect: same lease
        return [_internal(state, "access_granted", {"mode": existing["mode"], "lease_id": existing["id"]})]

    def deny(reason: str, maintainer_action: bool = False) -> list[Event]:
        return [_internal(state, "access_denied", {"login": login, "reason": reason,
                                                   "maintainer_action": maintainer_action})]

    if len(leases.active_for(state.repo, login)) >= policy.access.max_active_leases_per_person:
        return deny("you already have an active MergeMind fix in this repository; finish it or "
                    "reply `/mergemind withdraw` there first")
    min_days = policy.access.min_account_age / 86400
    if min_days > 0 and access.account_age_days(ctx.gh, login) < min_days:
        return deny(f"accounts must be at least {min_days:.0f} days old to receive access; "
                    "a maintainer can still review a pull request from a fork")

    mode = policy.access.mode
    status = "active"
    if mode == "collaborator":
        perm = access.current_permission(ctx.gh, state.repo, login)
        if perm in ("admin", "maintain", "write"):
            mode = "existing"  # never revoke access we didn't grant
        else:
            branch = ctx.gh.get_repo(state.repo).get("default_branch", "main")
            rules = access.check_branch_rules(ctx.gh, state.repo, branch)
            if not rules.ok:
                return deny(f"the `{branch}` branch isn't protected yet ({'; '.join(rules.missing)}). "
                            "A maintainer has been asked to fix this", maintainer_action=True)
            status = "invited" if access.invite(ctx.gh, state.repo, login, policy.access.permission) == "invited" else "active"
    lease_id = leases.create(state.repo, state.number, login, mode, status, ttl)
    events = [_internal(state, "access_granted", {"mode": mode, "lease_id": lease_id})]
    if mode == "collaborator" and status == "active":  # was already a read-level collaborator
        events.append(_internal(state, "access_accepted", {"lease_id": lease_id}))
    return events


@handler("post_briefing")
def post_briefing(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    import json as _json

    from mergemind.github.access import Leases
    from mergemind.github.briefing import render_briefing

    report = _report(ctx, state)
    lease = Leases(ctx.store).current(state.repo, state.number)
    if report is None or lease is None:
        return []
    run = ctx.store.conn.execute("SELECT as_of_sha FROM runs WHERE id=?", (state.data.get("report_run_id"),)).fetchone()
    sha = run["as_of_sha"] if run else None
    prof = ctx.store.conn.execute(
        "SELECT meta_json FROM repo_profiles WHERE repo_id=? AND sha=?", (ctx.store.repo_id(state.repo), sha)).fetchone()
    tests = _json.loads(prof["meta_json"]).get("test_commands", []) if prof else []
    text = render_briefing(repo=state.repo, number=state.number, login=lease["login"], report=report, sha=sha,
                           mode=lease["mode"], expires_at=lease["expires_at"], test_commands=tests)
    text = _guarded(ctx, state, text, "briefing", use_critic=_llm_written(ctx, state),
                    allowed_mentions={lease["login"]}) or HELD_NOTE
    _post_once(ctx, state, eff, text)
    return []


# --------------------------------------------------------------------------- PRs

STATUS_CONTEXT = "mergemind/verify"


def _set_status(ctx: Context, state: IssueState, sha: str | None, status: str, description: str) -> None:
    """Commit status on the PR head (best effort; needs Commit statuses: write)."""
    if not sha or ctx.dry_run or ctx.autonomy(state.repo) == "observe":
        return
    try:
        ctx.gh.request("POST", f"/repos/{state.repo}/statuses/{sha}", point="gh.status",
                       json_body={"state": status, "context": STATUS_CONTEXT, "description": description[:140]})
    except GitHubError as e:
        log.warning("[%s#%d] commit status not set: %s", state.repo, state.number, e.error_class)


@handler("analyse_pr")
def analyse_pr_effect(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.code.repo_cache import GitError
    from mergemind.pr.analyse import Waiting, analyse_pr

    p = eff.payload
    policy = ctx.policy(state.repo)
    try:
        res = analyse_pr(settings=ctx.settings, store=ctx.store, gh=ctx.gh, llm=ctx.llm, policy=policy,
                         repo=state.repo, issue_number=state.number, pr_number=p["pr_number"],
                         report=_report(ctx, state), scope_files=state.data.get("scope_files") or [],
                         ci_timed_out=bool(p.get("ci_timed_out")), cache=ctx.cache)
    except GitError as e:
        log.warning("[%s#%d] PR analysis failed: %s", state.repo, state.number, e)
        return [_internal(state, "pr_verdict", {"decision": "escalate", "head_sha": p.get("head_sha"),
                                                "reason": "analysis_failed"})]
    if isinstance(res, Waiting):
        _set_status(ctx, state, res.head_sha, "pending", "waiting for mergemind-verify test results")
        pending = ctx.store.conn.execute(
            "SELECT 1 FROM timers WHERE status='pending' AND kind='pr_ci_timeout'"
            " AND json_extract(ref_json,'$.repo')=? AND json_extract(ref_json,'$.number')=?",
            (state.repo, state.number)).fetchone()
        if not pending:
            ctx.store.add_timer(time.time() + policy.scaled(policy.pr.ci_timeout), "pr_ci_timeout",
                                {"repo": state.repo, "number": state.number, "timer_kind": "pr_ci_timeout"})
        return [_internal(state, "pr_waiting_ci", {"head_sha": res.head_sha})]
    v = res.verdict
    status = {"ready_for_maintainer": ("success", "verified; waiting for maintainer approval"),
              "changes_requested": ("failure", "changes requested")}.get(v.decision, ("pending", "on hold for maintainer review"))
    _set_status(ctx, state, v.head_sha, *status)
    return [_internal(state, "pr_verdict", {"decision": v.decision, "head_sha": v.head_sha, "run_id": res.run_id,
                                            "overridable": v.overridable})]


def _verdict(ctx: Context, state: IssueState):
    from mergemind.pr.analyse import PRVerdict

    run_id = state.data.get("verdict_run_id")
    row = ctx.store.conn.execute("SELECT verdict_json FROM runs WHERE id=?", (run_id,)).fetchone() if run_id else None
    return PRVerdict.model_validate_json(row["verdict_json"]) if row and row["verdict_json"] else None


def _branch_only_commits(ctx: Context, repo: str, base_ref: str, head: str) -> list[dict]:
    """Commits on `head` that are not on the base branch (the PR's own commits)."""
    cmp = ctx.gh.request("GET", f"/repos/{repo}/compare/{base_ref}...{head}", point="gh.compare")
    return cmp.get("commits") or []


def _update_only(ctx: Context, repo: str, old_head: str, new_head: str, base_ref: str) -> tuple[bool, str]:
    """True if the only change from old_head to new_head is one GitHub-made merge of the base branch.

    Compared against the base: the commits merged in *from* the base are already on it, so the
    PR's own commits before and after the update must differ by exactly that merge commit.
    (Comparing old_head...new_head directly would also list every merged-in base commit.)"""
    before = {c["sha"] for c in _branch_only_commits(ctx, repo, base_ref, old_head)}
    after = _branch_only_commits(ctx, repo, base_ref, new_head)
    added = [c for c in after if c["sha"] not in before]
    if len(added) != 1:
        return False, (f"{len(added)} commits not on {base_ref} were added since approval, "
                       "expected only GitHub's update merge")
    c = added[0]
    if c["sha"] != new_head:
        return False, "the PR head is not the update merge itself"
    parents = [x["sha"] for x in c.get("parents", [])]
    if len(parents) != 2 or parents[0] != old_head:
        return False, "the new commit is not a merge of the base branch into the approved commit"
    if ((c.get("committer") or {}).get("login") or "") != "web-flow":
        return False, "the update merge was not made by GitHub's update-branch"
    base_cmp = ctx.gh.request("GET", f"/repos/{repo}/compare/{parents[1]}...{base_ref}", point="gh.compare")
    if base_cmp.get("status") not in ("identical", "ahead"):
        return False, "the merged-in commit is not part of the base branch"
    return True, ""


@handler("merge_pr")
def merge_pr(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.pr.checks import check_runs, summarize

    p = eff.payload
    n, sha = p["pr_number"], p["sha"]
    policy = ctx.policy(state.repo)

    def result(**kw: Any) -> list[Event]:
        return [_internal(state, "merge_result", kw)]

    pr = ctx.gh.get_pull(state.repo, n)
    if pr["state"] != "open":
        return result(merged=False, reason="closed", detail="the pull request is no longer open")
    head, base_ref = pr["head"]["sha"], pr["base"]["ref"]
    if not sha or head != sha:
        return result(merged=False, reason="head_moved", detail="new commits were pushed after the approval")
    if p.get("carry_from"):
        ok, why = _update_only(ctx, state.repo, p["carry_from"], head, base_ref)
        if not ok:
            return result(merged=False, reason="carry_invalid", detail=why)
    v = _verdict(ctx, state)
    if v is None or v.head_sha != head or (v.decision != "ready_for_maintainer" and not state.data.get("override")):
        return result(merged=False, reason="head_moved", detail="the verification is for a different commit")

    # verified against today's base branch? If not, bring the PR up to date and verify again
    base_now = ctx.gh.request("GET", f"/repos/{state.repo}/branches/{base_ref}", point="gh.branch")["commit"]["sha"]
    if v.base_sha:
        base_moved = base_now != v.base_sha
    else:
        # verdict without a recorded base (older runs): ask GitHub whether the PR is behind the base
        cmp = ctx.gh.request("GET", f"/repos/{state.repo}/compare/{head}...{base_now}", point="gh.compare")
        base_moved = int(cmp.get("ahead_by") or 0) > 0
    if base_moved:
        try:
            ctx.gh.request("PUT", f"/repos/{state.repo}/pulls/{n}/update-branch", point="gh.update_branch",
                           json_body={"expected_head_sha": head})
        except GitHubError as e:
            if e.status in (409, 422):
                return result(merged=False, reason="conflict", detail="the PR conflicts with the latest base branch")
            raise
        return [_internal(state, "merge_updating", {"from_head": head})]

    ci = summarize(check_runs(ctx.gh, state.repo, head))
    if ci.status != "complete" or ci.tests != "success":
        return result(merged=False, reason="checks", detail="tests are not green on the approved commit")

    # record the maintainer's approval as a review, so "1 approval" branch rules are satisfied
    approver = p.get("approved_by") or state.approved_by
    try:
        ctx.gh.request("POST", f"/repos/{state.repo}/pulls/{n}/reviews", point="gh.review", json_body={
            "event": "APPROVE", "commit_id": head,
            "body": f"Approved by `{approver}` with `/mergemind approve` after MergeMind verification of `{head[:10]}`."})
    except GitHubError as e:
        log.info("[%s#%d] approval review not recorded: %s", state.repo, state.number, e.error_class)

    try:
        res = ctx.gh.request("PUT", f"/repos/{state.repo}/pulls/{n}/merge", point="gh.merge", json_body={
            "sha": head,  # GitHub refuses if the head moved in the meantime
            "merge_method": policy.pr.merge_method,
            "commit_title": f"{pr.get('title') or 'MergeMind fix'} (#{n})",
            "commit_message": f"Fixes #{state.number}\n\nVerified by MergeMind at {head[:10]}; approved by {approver}."})
    except GitHubError as e:
        if e.status == 409:
            return result(merged=False, reason="head_moved", detail="the branch changed during the merge")
        if e.status == 405:
            return result(merged=False, reason="blocked", detail=f"GitHub refused the merge ({str(e)[:160]})")
        raise
    return result(merged=True, merge_sha=res.get("sha"))


POST_MERGE_POLL_S = 60
POST_MERGE_MAX_ATTEMPTS = 30


@handler("check_post_merge_ci")
def check_post_merge_ci(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.pr.checks import check_runs

    sha = state.data.get("merged_sha")
    attempt = int(eff.payload.get("attempt") or 1)
    runs = check_runs(ctx.gh, state.repo, sha) if sha else []
    status = ctx.gh.request("GET", f"/repos/{state.repo}/commits/{sha}/status", point="gh.status_read") if sha else {}
    statuses = [s for s in (status or {}).get("statuses", []) if s.get("context") != "mergemind/verify"]

    def again() -> list[Event]:
        policy = ctx.policy(state.repo)
        ctx.store.add_timer(time.time() + policy.scaled(POST_MERGE_POLL_S), "post_merge_check",
                            {"repo": state.repo, "number": state.number, "timer_kind": "post_merge_check",
                             "attempt": attempt + 1})
        return []

    if not runs and not statuses:
        if attempt < 3:
            return again()  # CI on the default branch may not have started yet
        return [_internal(state, "post_merge_ci", {"conclusion": "none"})]
    failed = [r["name"] for r in runs if r.get("conclusion") in ("failure", "timed_out")] + \
             [s["context"] for s in statuses if s.get("state") in ("failure", "error")]
    if failed:
        return [_internal(state, "post_merge_ci", {"conclusion": "failure", "failed": failed[:10]})]
    pending = any(r.get("status") != "completed" for r in runs) or any(s.get("state") == "pending" for s in statuses)
    if pending:
        if attempt < POST_MERGE_MAX_ATTEMPTS:
            return again()
        return [_internal(state, "post_merge_ci", {"conclusion": "none"})]
    return [_internal(state, "post_merge_ci", {"conclusion": "success"})]


@handler("open_revert_pr")
def open_revert_pr(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.code.repo_cache import RepoCache

    sha = eff.payload.get("merge_sha") or state.data.get("merged_sha")
    v = _verdict(ctx, state)
    base_ref = v.base_ref if v else ctx.gh.get_repo(state.repo).get("default_branch", "main")
    branch = f"mergemind/revert-{sha[:8]}"
    cache = ctx.cache or RepoCache(ctx.settings.work_dir, ctx.settings.github_token)
    cache.revert_and_push(state.repo, sha, base_ref, branch)
    pr = ctx.gh.request("POST", f"/repos/{state.repo}/pulls", point="gh.create_pr", json_body={
        "title": f"Revert {sha[:8]} (default-branch CI failed after merging #{state.pr_number})",
        "head": branch, "base": base_ref,
        "body": f"The default branch's CI failed after MergeMind merged #{state.pr_number} for #{state.number}.\n\n"
                "This PR reverts that merge. MergeMind never merges reverts itself: a maintainer should review "
                "the failure and decide whether to merge this revert or fix forward."})
    _post_once(ctx, state, eff, f"Default-branch CI failed after the merge. Revert proposed in #{pr['number']}; "
                                "a maintainer will decide.")
    return []


@handler("post_pr_review")
def post_pr_review(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.pr.analyse import PRVerdict
    from mergemind.pr.render import PR_CARD, render_pr_comment, render_private_details

    run_id = eff.payload.get("run_id") or state.data.get("verdict_run_id")
    row = ctx.store.conn.execute("SELECT verdict_json FROM runs WHERE id=?", (run_id,)).fetchone() if run_id else None
    if not row or not row["verdict_json"] or state.pr_number is None:
        # escalated without an analysis run (e.g. git failure): say only that a human will look
        text = f"### MergeMind · ⏸ On hold for maintainer review\nMergeMind couldn't finish checking this PR.\n"
        v = None
    else:
        v = PRVerdict.model_validate_json(row["verdict_json"])
        contributor = state.contributor or state.data.get("pr_author")
        text = render_pr_comment(v, state.number, contributor)
        allowed = set(state.data.get("scope_files") or []) | set(v.changed_files)
        llm_text = bool(v.criteria or v.issues_found)
        guarded = ctx.guard(state.repo).check(
            text, kind="pr_review", allowed_files=allowed or None, allowed_mentions={contributor} if contributor else set(),
            context=f"Issue #{state.number}: {(_report(ctx, state) or TriageReport(issue_type='bug', summary=state.title, disposition='accept')).summary}",
            use_critic=llm_text, run_id=run_id)
        text = guarded.text if guarded.postable else HELD_NOTE
        if v.decision != "ready_for_maintainer" or not guarded.postable:
            _private_note(ctx, state, f"PR #{v.pr_number} {v.decision}", render_private_details(v))
    if state.pr_number is None:
        return []
    body = f"{text}\n{PR_CARD}" if PR_CARD not in text else text
    existing = next((c for c in ctx.gh.list_issue_comments(state.repo, state.pr_number)
                     if PR_CARD in (c.get("body") or "")), None)
    if existing:
        ctx.gh.update_comment(state.repo, existing["id"], body)
    else:
        ctx.gh.create_comment(state.repo, state.pr_number, body)
    return []


@handler("revoke_access")
def revoke_access(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    from mergemind.github import access

    leases = access.Leases(ctx.store)
    lease = leases.current(state.repo, state.number)
    if lease is None:
        return []
    reason = eff.payload.get("reason", "revoked")
    if lease["mode"] == "collaborator":
        access.revoke(ctx.gh, state.repo, lease["login"])
    leases.set_status(lease["id"], {"merged": "merged", "expired": "expired"}.get(reason, "revoked"))
    if reason in ("expired", "maintainer", "declined"):
        _post_once(ctx, state, eff, TEMPLATES["access_ended"].format(reason=reason))
    return []


@handler("notify_maintainers")
def notify_maintainers(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    policy = ctx.policy(state.repo)
    channel = policy.guard.maintainer_channel
    reason = eff.payload.get("reason", "attention")
    key = eff.key(state)
    if channel:
        # private repo issue; the contributor can't see it
        _private_note(ctx, state, reason, f"Phase: {state.phase.value}\nReason: {reason}\n{marker(key)}")
    elif not eff.payload.get("private"):
        people = policy.escalate_to or policy.maintainers
        names = " ".join(f"@{m}" for m in people)
        text = _guarded(ctx, state, f"{names} MergeMind needs a maintainer here ({reason}).", "notify",
                        use_critic=False, allowed_mentions=set(people))
        if names and text and not _marker_exists(ctx, state, key):
            ctx.gh.create_comment(state.repo, state.number, f"{text}\n{marker(key)}")
    else:
        log.warning("[%s#%d] private notification (%s) but no maintainer_channel configured",
                    state.repo, state.number, reason)
    return []


@handler("cancel_timers")
def cancel_timers(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    ctx.store.cancel_timers(state.repo, state.number, eff.payload.get("kinds"))
    return []


@handler("schedule_timer")
def schedule_timer(ctx: Context, state: IssueState, eff: Effect) -> list[Event]:
    p = eff.payload
    extras = {k: v for k, v in p.items() if k not in ("kind", "after_s")}
    ctx.store.add_timer(time.time() + float(p["after_s"]), p["kind"],
                        {"repo": state.repo, "number": state.number, "timer_kind": p["kind"], **extras})
    return []


def _marker_exists(ctx: Context, state: IssueState, key: str) -> bool:
    m = marker(key)
    return any(m in (c.get("body") or "") for c in ctx.gh.list_issue_comments(state.repo, state.number))


class _Default(dict):
    def __missing__(self, k: str) -> str:
        return ""
