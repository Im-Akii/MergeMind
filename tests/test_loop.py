"""End-to-end tick with a fake GitHub and a fake Nemotron."""

import itertools
import json

from mergemind.config import Policy, Settings
from mergemind.engine.effects import Context
from mergemind.engine.loop import tick
from mergemind.github.client import GitHubError
from mergemind.github.render import CARD_MARKER
from mergemind.models import Phase
from mergemind.store.db import Store
from tests.test_basics import fake_llm

_ids = itertools.count(100)


class FakeGitHub:
    def __init__(self, store, issues, perms=None, fail_comment=None):
        self.store = store
        self.issues = issues
        self.comments: dict[int, list[dict]] = {n: [] for n in issues}
        self.perms = perms or {}
        self.fail_comment = fail_comment

    def issues_since(self, repo, since):
        return [i for i in self.issues.values() if not since or i["updated_at"] > since]

    def get_issue(self, repo, n):
        return self.issues[n]

    def list_issue_comments(self, repo, n):
        return self.comments[n]

    def permission(self, repo, login):
        return self.perms.get(login, "read")

    def get_file(self, repo, path, ref=None):
        return None

    def request(self, method, path, **kw):
        if path.endswith("/pulls"):
            return []
        raise AssertionError(f"unexpected {method} {path}")

    def create_comment(self, repo, n, body):
        if self.fail_comment:
            raise self.fail_comment
        c = {"id": next(_ids), "body": body, "user": {"login": "mergemind[bot]", "type": "Bot"}}
        self.comments[n].append(c)
        return c

    def update_comment(self, repo, cid, body):
        for cs in self.comments.values():
            for c in cs:
                if c["id"] == cid:
                    c["body"] = body
        return {"id": cid}

    def user_comment(self, n, login, body, assoc="NONE"):
        self.comments[n].append({"id": next(_ids), "body": body, "user": {"login": login, "type": "User"},
                                 "author_association": assoc})
        self.issues[n]["comments"] = len(self.comments[n])
        self.issues[n]["updated_at"] = f"2026-10-10T00:00:{len(self.comments[n]):02d}Z"


def issue(n, body="App crashes on start", state="open"):
    return {"number": n, "title": "crash", "body": body, "user": {"login": "rita", "type": "User"},
            "state": state, "comments": 0, "updated_at": "2026-10-10T00:00:00Z", "locked": False}


def make_ctx(tmp_path, gh_factory, llm_outputs, autonomy="suggest"):
    store = Store(tmp_path / "m.db")
    gh = gh_factory(store)
    llm, _ = fake_llm(llm_outputs)
    ctx = Context(settings=Settings(), store=store, gh=gh, llm=llm,
                  policies={"o/r": Policy(autonomy=autonomy, maintainers=["alice"])})
    return ctx, gh


NEEDS_INFO = json.dumps({"issue_type": "bug", "summary": "Crash on start.", "disposition": "needs_info",
                         "missing_info": [{"field": "version", "why": "can't reproduce"}], "confidence": 0.8})


def test_tick_triages_and_posts_one_card(tmp_path):
    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}), [NEEDS_INFO])
    tick(ctx, ["o/r"])
    assert ctx.store.load_issue("o/r", 1).phase == Phase.NEEDS_INFO
    cards = [c for c in gh.comments[1] if CARD_MARKER in c["body"]]
    # too little detail: the no-LLM gate decides, asking for a description
    assert len(cards) == 1 and "description" in cards[0]["body"]
    # a second tick with no changes does nothing
    assert tick(ctx, ["o/r"])["processed"] == 0
    assert len([c for c in gh.comments[1] if CARD_MARKER in c["body"]]) == 1


def test_reporter_reply_retriggers_triage(tmp_path):
    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}), [NEEDS_INFO, NEEDS_INFO])
    tick(ctx, ["o/r"])
    gh.user_comment(1, "rita", "version 2.3.1 on Windows")
    tick(ctx, ["o/r"])
    runs = ctx.store.conn.execute("SELECT COUNT(*) FROM runs WHERE kind='triage'").fetchone()[0]
    assert runs == 2


def test_non_writer_cannot_stop(tmp_path):
    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}), [NEEDS_INFO])
    tick(ctx, ["o/r"])
    gh.user_comment(1, "mallory", "/mergemind stop")
    tick(ctx, ["o/r"])
    assert not ctx.store.load_issue("o/r", 1).data.get("stopped")
    assert any("write permission required" in c["body"] for c in gh.comments[1])


def test_observe_mode_writes_nothing(tmp_path):
    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}), [NEEDS_INFO], autonomy="observe")
    tick(ctx, ["o/r"])
    assert ctx.store.load_issue("o/r", 1).phase == Phase.NEEDS_INFO
    assert gh.comments[1] == []


def test_permission_error_blocks_without_retry(tmp_path):
    err = GitHubError(403, "permission", "Resource not accessible")
    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}, fail_comment=err), [NEEDS_INFO])
    tick(ctx, ["o/r"])
    assert ctx.store.load_issue("o/r", 1).phase == Phase.PERMISSION_BLOCKED
    assert ctx.store.conn.execute("SELECT COUNT(*) FROM timers").fetchone()[0] == 0


def test_rate_limit_schedules_retry_timer(tmp_path):
    err = GitHubError(429, "rate_limited", "slow down", reset_at=10**10)
    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}, fail_comment=err), [NEEDS_INFO])
    tick(ctx, ["o/r"])
    row = ctx.store.conn.execute("SELECT kind, due_at FROM timers").fetchone()
    assert row["kind"] == "retry_effect" and row["due_at"] == 10**10


def test_card_with_secret_never_reaches_github(tmp_path):
    """A model-written summary that leaks a secret is held; the public card says so, nothing more."""
    from mergemind.engine.effects import update_card
    from mergemind.models import Effect, IssueState, Phase, TriageReport

    secret = "ghp_" + "Zr8Kq2Lm9Nx4Vb7Ct1Wy5Ue3Pa6Sd0Fg2Hj4"
    critic_ok = json.dumps({"reveals_extra": False, "items": []})
    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}), [critic_ok])
    report = TriageReport(issue_type="bug", summary=f"Fails because the CI token {secret} expired.",
                          disposition="accept", disposition_confidence=0.9)
    run_id = ctx.store.start_run("o/r", 1, "triage", prompt_version="investigator-v1")
    ctx.store.finish_run(run_id, "ok", report_json=report.model_dump_json())
    state = IssueState(repo="o/r", number=1, phase=Phase.ACCEPTED, data={"report_run_id": run_id})
    update_card(ctx, state, Effect(kind="update_card"))
    posted = "\n".join(c["body"] for c in gh.comments[1])
    assert secret not in posted and "[REDACTED]" in posted


def test_card_with_security_wording_is_held(tmp_path):
    from mergemind.engine.effects import HELD_NOTE, update_card
    from mergemind.models import Effect, IssueState, Phase, TriageReport

    ctx, gh = make_ctx(tmp_path, lambda s: FakeGitHub(s, {1: issue(1)}), [])
    report = TriageReport(issue_type="bug", summary="An attacker could use SQL injection in the search box.",
                          disposition="accept", disposition_confidence=0.9)
    run_id = ctx.store.start_run("o/r", 1, "triage", prompt_version="investigator-v1")
    ctx.store.finish_run(run_id, "ok", report_json=report.model_dump_json())
    state = IssueState(repo="o/r", number=1, phase=Phase.ACCEPTED, data={"report_run_id": run_id})
    update_card(ctx, state, Effect(kind="update_card"))
    posted = "\n".join(c["body"] for c in gh.comments[1])
    assert HELD_NOTE in posted and "SQL injection" not in posted
    assert ctx.store.conn.execute("SELECT decision FROM guard_log").fetchone()[0] == "held_for_maintainer"
