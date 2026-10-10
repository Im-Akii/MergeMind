"""The pure state machine: transition(state, event, policy, now) -> (new_state, effects).

No I/O here. Everything with side effects is described as an Effect and executed by
engine/effects.py. Authorization facts (e.g. `can_write`) are resolved by the ingestor
and arrive inside the event payload.
"""

from __future__ import annotations

from mergemind.config import Policy
from mergemind.models import TERMINAL, Effect, Event, IssueState, Phase

P = Phase

# disposition from a verified TriageReport -> phase
DISPOSITION_PHASE = {
    "needs_info": P.NEEDS_INFO,
    "duplicate": P.DUPLICATE_SUGGESTED,
    "question": P.QUESTION,
    "feature_review": P.FEATURE_REVIEW,
    "out_of_scope": P.OUT_OF_SCOPE_SUGGESTED,
    "security": P.SECURITY_ESCALATED,
    "escalate": P.ESCALATED,
    "accept": P.ACCEPTED,
}

TRIAGE_REENTRY = {P.NEW, P.NEEDS_INFO, P.DUPLICATE_SUGGESTED, P.ESCALATED}
ACCESS_PHASES = {P.ACCESS_GRANTED, P.WORKING}
PR_PHASES = {P.PR_ANALYSING, P.CHANGES_REQUESTED, P.SAFETY_HOLD, P.READY_FOR_MAINTAINER, P.MERGING}

AUTONOMY_ORDER = ["observe", "suggest", "invite", "merge"]


def _at_least(policy: Policy, level: str, autonomy: str | None = None) -> bool:
    current = autonomy or policy.autonomy
    return AUTONOMY_ORDER.index(current) >= AUTONOMY_ORDER.index(level)


def _go(state: IssueState, phase: Phase, **updates: object) -> IssueState:
    return state.model_copy(update={"phase": phase, **updates})


def _card(state: IssueState) -> Effect:
    return Effect(kind="update_card", payload={"phase": state.phase.value})


def transition(
    state: IssueState,
    evt: Event,
    policy: Policy,
    now: float,
    autonomy: str | None = None,
) -> tuple[IssueState, list[Effect]]:
    """Return the next state and the effects to run. Never mutates `state`."""
    kind, p = evt.kind, evt.payload
    autonomy = autonomy or policy.autonomy

    # ---- global rules --------------------------------------------------------
    if kind in ("issue_closed", "issue_locked", "issue_gone"):
        if state.phase in TERMINAL:
            return state, []
        effects = [Effect(kind="cancel_timers")]
        if state.contributor and state.phase in ACCESS_PHASES | PR_PHASES:
            effects.append(Effect(kind="revoke_access", payload={"login": state.contributor}))
        return _go(state, P.CLOSED), effects

    if kind == "issue_reopened" and state.phase == P.CLOSED:
        return _go(state, P.TRIAGING, contributor=None, pr_number=None), [Effect(kind="run_triage")]

    if kind == "write_forbidden":  # 403 on any write: no retry, explain
        return _go(state, P.PERMISSION_BLOCKED, data={**state.data, "blocked": p}), [
            Effect(kind="update_card", payload={"phase": P.PERMISSION_BLOCKED.value}),
            Effect(kind="notify_maintainers", payload={"reason": "permission", **p}),
        ]

    if state.phase in TERMINAL:
        return state, []

    if kind == "command":
        return _command(state, p, policy, now, autonomy)

    # ---- triage -------------------------------------------------------------
    if kind == "issue_opened" and state.phase == P.NEW:
        s = _go(state, P.TRIAGING, title=p.get("title", ""), author=p.get("author", ""))
        return s, [Effect(kind="run_triage")]

    if kind == "issue_edited" and state.phase in TRIAGE_REENTRY:
        return _go(state, P.TRIAGING), [Effect(kind="run_triage")]

    if kind == "comment" and state.phase == P.NEEDS_INFO and p.get("author") == state.author:
        return _go(state, P.TRIAGING), [Effect(kind="run_triage")]

    if kind == "triage_done" and state.phase == P.TRIAGING:
        disposition = p["disposition"]
        phase = DISPOSITION_PHASE.get(disposition, P.ESCALATED)
        s = _go(
            state,
            phase,
            disposition=disposition,
            data={**state.data, "report_run_id": p.get("run_id"), "difficulty": p.get("difficulty"),
                  "scope_files": p.get("scope_files", [])},
        )
        effects = [_card(s)]
        if phase == P.SECURITY_ESCALATED:
            effects.append(Effect(kind="notify_maintainers", payload={"reason": "security", "private": True}))
        elif phase == P.ESCALATED:
            effects.append(Effect(kind="notify_maintainers", payload={"reason": "escalated"}))
        elif phase == P.ACCEPTED:
            s, more = _after_accept(s, policy, autonomy)
            effects = [_card(s), *more]
        return s, effects

    if kind == "triage_failed" and state.phase == P.TRIAGING:
        s = _go(state, P.ESCALATED, disposition="escalate")
        return s, [_card(s), Effect(kind="notify_maintainers", payload={"reason": "triage_failed", **p})]

    # ---- access -------------------------------------------------------------
    if kind == "access_granted" and state.phase == P.ACCESS_OFFERED:
        mode = p.get("mode", "collaborator")
        # fork / already-has-access: nothing to accept, work starts now
        phase = P.ACCESS_GRANTED if mode == "collaborator" else P.WORKING
        s = _go(state, phase, data={**state.data, "access_mode": mode, "lease_id": p.get("lease_id"),
                                    "last_activity": now})
        return s, [_card(s), Effect(kind="post_briefing", payload={"lease_id": p.get("lease_id")}),
                   *_lease_timers(policy, now)]

    if kind == "access_denied" and state.phase == P.ACCESS_OFFERED:
        s = state.model_copy(update={"contributor": None})
        effects = [Effect(kind="post_comment", payload={"template": "access_denied", "reason": p.get("reason", ""),
                                                        "to": p.get("login")})]
        if p.get("maintainer_action"):
            effects.append(Effect(kind="notify_maintainers", payload={"reason": f"access blocked: {p['reason']}"}))
        return s, effects

    if kind == "access_accepted" and state.phase == P.ACCESS_GRANTED:
        s = _go(state, P.WORKING, data={**state.data, "last_activity": now})
        return s, [_card(s)]

    if kind == "invitation_gone" and state.phase == P.ACCESS_GRANTED:
        # declined or expired invitation (GitHub expires them after 7 days)
        s = _go(state, P.ACCEPTED_UNCLAIMED, contributor=None)
        return s, [_card(s), Effect(kind="cancel_timers"), Effect(kind="revoke_access", payload={"reason": "declined"})]

    if kind in ("contributor_activity", "comment") and state.phase in ACCESS_PHASES:
        if kind == "comment" and (p.get("author") or "").lower() != (state.contributor or "").lower():
            return state, []
        return state.model_copy(update={"data": {**state.data, "last_activity": now}}), []

    if kind == "timer":
        return _timer(state, p, policy, now)

    # ---- pull request -------------------------------------------------------
    if kind == "pr_opened" and state.phase in ACCESS_PHASES | {P.ACCEPTED, P.ACCEPTED_UNCLAIMED}:
        s = _go(state, P.PR_ANALYSING, pr_number=p["pr_number"], approved_by=None, approved_head_sha=None,
                data={**state.data, "pr_head_sha": p.get("head_sha"), "pr_author": p.get("author")})
        # access stays until the PR is merged or closed
        return s, [_card(s), Effect(kind="cancel_timers", payload={"kinds": ["access_checkin", "access_expire"]}),
                   Effect(kind="analyse_pr", payload={"pr_number": p["pr_number"], "head_sha": p.get("head_sha")})]

    if kind == "pr_updated" and state.phase in PR_PHASES and p.get("pr_number") == state.pr_number:
        if p.get("head_sha") and p.get("head_sha") == state.data.get("pr_head_sha"):
            return state, []  # same commit we already know about
        # any new push voids an approval and restarts analysis
        s = _go(state, P.PR_ANALYSING, approved_by=None, approved_head_sha=None,
                data={**state.data, "pr_head_sha": p.get("head_sha")})
        effects = [_card(s), Effect(kind="analyse_pr", payload={"pr_number": state.pr_number,
                                                                 "head_sha": p.get("head_sha")})]
        if state.approved_by and not state.data.get("carry_approval"):
            effects.append(Effect(kind="post_comment", payload={
                "template": "approval_voided", "reason": "new commits were pushed after the approval"}))
        return s, effects

    if kind == "pr_waiting_ci" and state.phase == P.PR_ANALYSING and p.get("head_sha") == state.data.get("pr_head_sha"):
        return state.model_copy(update={"data": {**state.data, "awaiting_ci_sha": p.get("head_sha")}}), []

    # CI finished: re-run analysis only if the last one was waiting on CI for this commit
    if kind == "pr_checks_completed" and state.phase == P.PR_ANALYSING and p.get("pr_number") == state.pr_number \
            and p.get("head_sha") == state.data.get("pr_head_sha") == state.data.get("awaiting_ci_sha"):
        s = state.model_copy(update={"data": {**state.data, "awaiting_ci_sha": None}})
        return s, [Effect(kind="analyse_pr", payload={"pr_number": state.pr_number, "head_sha": p.get("head_sha"),
                                                      "ci": "complete"})]

    if kind == "pr_merged" and p.get("pr_number") == state.pr_number and state.phase in PR_PHASES | {P.WORKING}:
        # merged by a human outside MergeMind
        s = _go(state, P.DONE, data={**state.data, "merged_sha": p.get("merge_sha"), "merged_externally": True})
        effects = [_card(s), Effect(kind="cancel_timers")]
        if state.contributor:
            effects.append(Effect(kind="revoke_access", payload={"login": state.contributor, "reason": "merged"}))
        return s, effects

    if kind == "pr_closed_unmerged" and state.phase in PR_PHASES and p.get("pr_number") == state.pr_number:
        s = _go(state, P.WORKING if state.contributor else P.ACCEPTED_UNCLAIMED, pr_number=None,
                approved_by=None, approved_head_sha=None)
        return s, [_card(s), *(_lease_timers(policy, now) if state.contributor else [])]

    if kind == "pr_verdict" and state.phase == P.PR_ANALYSING:
        if p.get("head_sha") and state.data.get("pr_head_sha") and p["head_sha"] != state.data["pr_head_sha"]:
            return state, []  # verdict for an older commit; a newer analysis is coming
        decision = p["decision"]
        data = {**state.data, "verdict_run_id": p.get("run_id"), "verified_head_sha": p.get("head_sha"),
                "overridable": bool(p.get("overridable"))}
        carry = state.data.get("carry_approval")
        if carry and decision == "ready_for_maintainer":
            # MergeMind updated the branch with the base; the change was re-verified, so the
            # maintainer's approval stands. merge_pr checks the update added nothing else.
            s = _go(state, P.MERGING, approved_by=carry["by"], approved_head_sha=p.get("head_sha"),
                    data={**data, "carry_approval": None})
            return s, [_card(s), Effect(kind="cancel_timers", payload={"kinds": ["pr_ci_timeout"]}),
                       Effect(kind="merge_pr", payload={"pr_number": state.pr_number, "sha": p.get("head_sha"),
                                                        "approved_by": carry["by"], "carry_from": carry["from_head"]})]
        data["carry_approval"] = None
        # an escalated PR keeps its PR context: it waits for a maintainer like a safety hold
        target = {
            "ready_for_maintainer": P.READY_FOR_MAINTAINER,
            "changes_requested": P.CHANGES_REQUESTED,
        }.get(decision, P.SAFETY_HOLD)
        s = _go(state, target, data=data)
        effects = [_card(s), Effect(kind="post_pr_review", payload={"decision": decision, "run_id": p.get("run_id")}),
                   Effect(kind="cancel_timers", payload={"kinds": ["pr_ci_timeout"]})]
        if carry:
            effects.append(Effect(kind="post_comment", payload={
                "template": "approval_voided", "reason": "the PR changed after it was brought up to date"}))
        if target == P.SAFETY_HOLD:
            effects.append(Effect(kind="notify_maintainers", payload={"reason": f"PR #{state.pr_number}: {decision}",
                                                                      "private": True}))
        return s, effects

    if kind == "merge_result" and state.phase == P.MERGING:
        if p.get("merged"):
            s = _go(state, P.POST_MERGE_WATCH, data={**state.data, "merged_sha": p.get("merge_sha")})
            return s, [_card(s), Effect(kind="post_comment", payload={"template": "merged", "by": state.approved_by}),
                       Effect(kind="schedule_timer", payload={
                           "kind": "post_merge_check", "after_s": policy.scaled(policy.pr.post_merge_watch),
                           "attempt": 1})]
        reason = p.get("reason", "unknown")
        if reason in ("blocked", "carry_invalid", "closed"):
            # GitHub refused (rules/reviews), the branch update brought in more than the base,
            # or the PR was closed: a maintainer decides; the approval is spent
            target = P.SAFETY_HOLD if reason != "blocked" else P.READY_FOR_MAINTAINER
            s = _go(state, target, approved_by=None, approved_head_sha=None,
                    data={**state.data, "carry_approval": None})
            return s, [_card(s), Effect(kind="post_comment", payload={"template": "merge_blocked",
                                                                      "reason": p.get("detail") or reason}),
                       Effect(kind="notify_maintainers", payload={"reason": f"merge blocked: {reason}"})]
        # head moved or conflict: the approval no longer applies → analyse again
        s = _go(state, P.PR_ANALYSING, approved_by=None, approved_head_sha=None)
        return s, [_card(s), Effect(kind="post_comment", payload={"template": "approval_voided",
                                                                  "reason": p.get("detail") or reason}),
                   Effect(kind="analyse_pr", payload={"pr_number": state.pr_number, "reason": reason})]

    if kind == "merge_updating" and state.phase == P.MERGING:
        # base moved since verification: MergeMind merged the base into the PR branch;
        # re-verify the result and keep the approval if nothing else changed
        s = _go(state, P.PR_ANALYSING,
                data={**state.data, "carry_approval": {"by": state.approved_by, "from_head": p.get("from_head")}})
        return s, [_card(s)]

    if kind == "post_merge_ci" and state.phase == P.POST_MERGE_WATCH:
        if p.get("conclusion") in ("success", "none"):
            s = _go(state, P.DONE, data={**state.data, "post_merge_ci": p.get("conclusion")})
            effects = [_card(s), Effect(kind="cancel_timers")]
            if state.contributor:
                effects.append(Effect(kind="revoke_access", payload={"login": state.contributor, "reason": "merged"}))
            return s, effects
        if p.get("conclusion") == "failure":
            s = _go(state, P.REVERT_PROPOSED)
            return s, [_card(s), Effect(kind="open_revert_pr", payload={"merge_sha": state.data.get("merged_sha")}),
                       Effect(kind="notify_maintainers", payload={"reason": "post_merge_ci_failed"})]

    return state, []


# --------------------------------------------------------------------------- helpers


def _after_accept(state: IssueState, policy: Policy, autonomy: str) -> tuple[IssueState, list[Effect]]:
    if not _at_least(policy, "suggest", autonomy):
        return state, []
    if not _at_least(policy, "invite", autonomy):
        s = _go(state, P.AWAITING_APPROVAL)
        return s, [Effect(kind="notify_maintainers", payload={"reason": "approve_access"})]
    s = _go(state, P.ACCESS_OFFERED)
    return s, [Effect(kind="offer_access", payload={"login": state.author})]


def _lease_timers(policy: Policy, now: float) -> list[Effect]:
    ttl = policy.scaled(policy.access.ttl)
    return [
        Effect(kind="schedule_timer", payload={"kind": "access_checkin", "after_s": ttl * policy.access.checkin_at}),
        Effect(kind="schedule_timer", payload={"kind": "access_expire", "after_s": ttl}),
    ]


def _timer(state: IssueState, p: dict, policy: Policy, now: float) -> tuple[IssueState, list[Effect]]:
    tk = p.get("timer_kind")
    if tk == "access_checkin" and state.phase in ACCESS_PHASES:
        last = state.data.get("last_activity")
        if last is None or now - last >= policy.scaled(policy.access.ttl) * policy.access.checkin_at:
            return state, [Effect(kind="post_comment", payload={"template": "checkin"})]
        return state, []
    if tk == "access_expire" and state.phase in ACCESS_PHASES:
        s = _go(state, P.ACCEPTED_UNCLAIMED, contributor=None)
        return s, [_card(s), Effect(kind="revoke_access", payload={"login": state.contributor, "reason": "expired"})]
    if tk == "post_merge_check" and state.phase == P.POST_MERGE_WATCH:
        return state, [Effect(kind="check_post_merge_ci", payload={"attempt": int(p.get("attempt") or 1)})]
    if tk == "pr_ci_timeout" and state.phase == P.PR_ANALYSING:
        return state, [Effect(kind="analyse_pr", payload={"pr_number": state.pr_number,
                                                          "head_sha": state.data.get("pr_head_sha"),
                                                          "ci_timed_out": True})]
    return state, []


def _command(
    state: IssueState, p: dict, policy: Policy, now: float, autonomy: str
) -> tuple[IssueState, list[Effect]]:
    name = p.get("name")
    author = p.get("author")
    can_write = bool(p.get("can_write"))
    ph = state.phase

    def deny(reason: str) -> tuple[IssueState, list[Effect]]:
        return state, [Effect(kind="post_comment", payload={"template": "denied", "reason": reason, "to": author})]

    if name == "stop":
        if not can_write:
            return deny("write permission required")
        s = state.model_copy(update={"data": {**state.data, "stopped": True}})
        return s, [Effect(kind="cancel_timers"), _card(s)]

    if name == "why":
        return state, [Effect(kind="post_comment", payload={"template": "why"})]

    if name == "contribute":
        if ph != P.ACCESS_OFFERED:
            return deny("this issue is not open for contribution right now")
        if author != state.author:
            return deny("only the issue author can request access for this issue")
        if not _at_least(policy, "invite", autonomy):
            return deny("autonomy level does not allow granting access")
        s = state.model_copy(update={"contributor": author})
        return s, [Effect(kind="grant_access", payload={"login": author})]

    if name == "withdraw":
        if author != state.contributor:
            return deny("only the contributor can withdraw")
        s = _go(state, P.ACCEPTED_UNCLAIMED, contributor=None)
        return s, [_card(s), Effect(kind="cancel_timers"), Effect(kind="revoke_access", payload={"login": author, "reason": "withdrawn"})]

    if name == "recheck":
        if ph in PR_PHASES and ph != P.MERGING and (can_write or author == state.contributor):
            s = _go(state, P.PR_ANALYSING, approved_by=None, approved_head_sha=None)
            return s, [_card(s), Effect(kind="analyse_pr", payload={"pr_number": state.pr_number})]
        return deny("nothing to re-check")

    # ---- maintainer-only commands ----
    if not can_write:
        return deny("write permission required")

    if name == "accept" and ph == P.FEATURE_REVIEW:
        s, effects = _after_accept(_go(state, P.ACCEPTED, disposition="accept"), policy, autonomy)
        return s, [_card(s), *effects]

    if name == "not-duplicate" and ph == P.DUPLICATE_SUGGESTED:
        return _go(state, P.TRIAGING), [Effect(kind="run_triage", payload={"not_duplicate": True})]

    if name == "approve-access" and ph == P.AWAITING_APPROVAL:
        s = _go(state, P.ACCESS_OFFERED)
        return s, [_card(s), Effect(kind="offer_access", payload={"login": state.author})]

    if name == "approve":
        override = ph == P.SAFETY_HOLD and state.data.get("overridable")
        if ph != P.READY_FOR_MAINTAINER and not override:
            if ph == P.SAFETY_HOLD:
                return deny("this hold can't be overridden by command (safety flags or missing CI); "
                            "review the PR and merge it manually if it is safe")
            return deny("the PR is not ready: MergeMind only merges after verification passes")
        if not _at_least(policy, "merge", autonomy):
            return deny("autonomy level does not allow merging")
        lowered = (author or "").lower()
        if lowered in {(state.contributor or "").lower(), (state.data.get("pr_author") or "").lower()} - {""}:
            return deny("the author of a change can't approve it")
        head = state.data.get("verified_head_sha")
        s = _go(state, P.MERGING, approved_by=author, approved_head_sha=head,
                data={**state.data, "carry_approval": None, "override": bool(override)})
        return s, [_card(s), Effect(kind="merge_pr", payload={"pr_number": state.pr_number, "sha": head,
                                                               "approved_by": author})]

    if name == "request-changes" and ph in (P.READY_FOR_MAINTAINER, P.SAFETY_HOLD, P.PR_ANALYSING):
        s = _go(state, P.CHANGES_REQUESTED, approved_by=None, approved_head_sha=None)
        return s, [_card(s), Effect(kind="post_comment", payload={"template": "maintainer_changes", "text": p.get("args", "")})]

    if name == "revoke" and state.contributor:
        s = _go(state, P.ACCEPTED_UNCLAIMED, contributor=None)
        return s, [_card(s), Effect(kind="cancel_timers"), Effect(kind="revoke_access", payload={"login": state.contributor, "reason": "maintainer"})]

    return deny(f"`{name}` is not valid in phase {ph.value}")
