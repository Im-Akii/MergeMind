from mergemind.config import Policy
from mergemind.engine.states import transition
from mergemind.models import Event, IssueState, Phase

POLICY = Policy(autonomy="merge", maintainers=["alice"])
N = 0


def ev(kind, **payload):
    global N
    N += 1
    return Event(repo="o/r", number=1, source="internal", kind=kind, external_id=f"e{N}", payload=payload)


def cmd(name, author, can_write=False, args=""):
    return ev("command", name=name, author=author, can_write=can_write, args=args)


def step(state, event, policy=POLICY, now=1000.0):
    new, effects = transition(state, event, policy, now)
    return new, [e.kind for e in effects], effects


def start():
    return IssueState(repo="o/r", number=1)


def accepted_and_offered():
    s, kinds, _ = step(start(), ev("issue_opened", title="crash", author="rita"))
    assert s.phase == Phase.TRIAGING and kinds == ["run_triage"]
    s, kinds, _ = step(s, ev("triage_done", disposition="accept", run_id=7))
    assert s.phase == Phase.ACCESS_OFFERED
    assert "offer_access" in kinds
    return s


def test_happy_path_to_done():
    s = accepted_and_offered()
    s, kinds, effects = step(s, cmd("contribute", "rita"))
    assert kinds == ["grant_access"] and s.contributor == "rita"
    s, kinds, _ = step(s, ev("access_granted"))
    assert s.phase == Phase.ACCESS_GRANTED and kinds.count("schedule_timer") == 2
    s, _, _ = step(s, ev("access_accepted"))
    assert s.phase == Phase.WORKING
    s, kinds, _ = step(s, ev("pr_opened", pr_number=9, author="rita"))
    assert s.phase == Phase.PR_ANALYSING and "analyse_pr" in kinds and s.pr_number == 9
    s, _, _ = step(s, ev("pr_verdict", decision="ready_for_maintainer", head_sha="abc", run_id=3))
    assert s.phase == Phase.READY_FOR_MAINTAINER
    s, kinds, effects = step(s, cmd("approve", "alice", can_write=True))
    assert s.phase == Phase.MERGING and s.approved_head_sha == "abc"
    merge = next(e for e in effects if e.kind == "merge_pr")
    assert merge.payload["sha"] == "abc"
    s, _, _ = step(s, ev("merge_result", merged=True, merge_sha="m1"))
    assert s.phase == Phase.POST_MERGE_WATCH
    s, kinds, _ = step(s, ev("post_merge_ci", conclusion="success"))
    assert s.phase == Phase.DONE and "revoke_access" in kinds


def test_only_author_can_contribute():
    s = accepted_and_offered()
    s2, kinds, effects = step(s, cmd("contribute", "mallory"))
    assert s2.contributor is None and kinds == ["post_comment"]
    assert effects[0].payload["template"] == "denied"


def test_approve_requires_write_and_ready():
    s = accepted_and_offered().model_copy(update={"phase": Phase.READY_FOR_MAINTAINER, "pr_number": 9})
    s2, kinds, _ = step(s, cmd("approve", "rita", can_write=False))
    assert s2.phase == Phase.READY_FOR_MAINTAINER and kinds == ["post_comment"]
    s3 = s.model_copy(update={"phase": Phase.CHANGES_REQUESTED})
    s4, kinds, _ = step(s3, cmd("approve", "alice", can_write=True))
    assert s4.phase == Phase.CHANGES_REQUESTED and kinds == ["post_comment"]


def test_push_after_approval_voids_it():
    s = start().model_copy(update={
        "phase": Phase.MERGING, "pr_number": 9, "approved_by": "alice", "approved_head_sha": "abc"})
    s2, kinds, _ = step(s, ev("pr_updated", pr_number=9, head_sha="def"))
    assert s2.phase == Phase.PR_ANALYSING and s2.approved_by is None and "analyse_pr" in kinds


def test_merge_failure_goes_back_to_analysis():
    s = start().model_copy(update={"phase": Phase.MERGING, "pr_number": 9, "approved_by": "alice"})
    s2, kinds, _ = step(s, ev("merge_result", merged=False, reason="head_moved"))
    assert s2.phase == Phase.PR_ANALYSING and s2.approved_by is None


def test_post_merge_red_opens_revert():
    s = start().model_copy(update={"phase": Phase.POST_MERGE_WATCH, "data": {"merged_sha": "m1"}})
    s2, kinds, _ = step(s, ev("post_merge_ci", conclusion="failure"))
    assert s2.phase == Phase.REVERT_PROPOSED and "open_revert_pr" in kinds


def test_close_revokes_access_and_is_terminal():
    s = start().model_copy(update={"phase": Phase.WORKING, "contributor": "rita"})
    s2, kinds, _ = step(s, ev("issue_closed"))
    assert s2.phase == Phase.CLOSED and "revoke_access" in kinds and "cancel_timers" in kinds
    s3, kinds, _ = step(s2, cmd("approve", "alice", can_write=True))
    assert s3 == s2 and kinds == []


def test_suggest_autonomy_waits_for_access_approval():
    policy = Policy(autonomy="suggest")
    s, _, _ = step(start(), ev("issue_opened", title="t", author="rita"), policy)
    s, kinds, _ = step(s, ev("triage_done", disposition="accept"), policy)
    assert s.phase == Phase.AWAITING_APPROVAL and "offer_access" not in kinds
    s, kinds, _ = step(s, cmd("approve-access", "alice", can_write=True), policy)
    assert s.phase == Phase.ACCESS_OFFERED and "offer_access" in kinds


def test_invite_autonomy_cannot_merge():
    policy = Policy(autonomy="invite")
    s = start().model_copy(update={"phase": Phase.READY_FOR_MAINTAINER, "pr_number": 9})
    s2, kinds, _ = step(s, cmd("approve", "alice", can_write=True), policy)
    assert s2.phase == Phase.READY_FOR_MAINTAINER and kinds == ["post_comment"]


def test_security_is_private():
    s, _, _ = step(start(), ev("issue_opened", title="t", author="rita"))
    s, _, effects = step(s, ev("triage_done", disposition="security"))
    assert s.phase == Phase.SECURITY_ESCALATED
    notify = next(e for e in effects if e.kind == "notify_maintainers")
    assert notify.payload["private"] is True


def test_access_expiry_revokes():
    s = start().model_copy(update={"phase": Phase.WORKING, "contributor": "rita"})
    s2, kinds, _ = step(s, ev("timer", timer_kind="access_expire"))
    assert s2.phase == Phase.ACCEPTED_UNCLAIMED and s2.contributor is None and "revoke_access" in kinds


def test_checkin_skipped_when_recently_active():
    policy = Policy(autonomy="merge", access={"ttl": "10d"})
    s = start().model_copy(update={"phase": Phase.WORKING, "contributor": "rita",
                                   "data": {"last_activity": 1000.0}})
    _, kinds, _ = step(s, ev("timer", timer_kind="access_checkin"), policy, now=1000.0 + 3600)
    assert kinds == []
    _, kinds, _ = step(s, ev("timer", timer_kind="access_checkin"), policy, now=1000.0 + 6 * 86400)
    assert kinds == ["post_comment"]


def test_transition_does_not_mutate_input():
    s = start()
    before = s.model_dump()
    transition(s, ev("issue_opened", title="t", author="a"), POLICY, 0.0)
    assert s.model_dump() == before
