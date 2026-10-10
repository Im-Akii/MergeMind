"""M4: /contribute → checks → invite → acceptance → briefing → check-in → expiry/revoke,
driven through the real tick loop against a fake GitHub."""

import itertools
import time

import pytest

from mergemind.config import Policy, Settings
from mergemind.engine.effects import Context
from mergemind.engine.loop import tick
from mergemind.github.client import GitHubError
from mergemind.models import IssueState, Phase, Region, TriageReport
from mergemind.store.db import Store

_ids = itertools.count(1000)
REPO = "o/r"
PROTECTED = [{"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
             {"type": "non_fast_forward"}, {"type": "deletion"}]


class FakeGH:
    def __init__(self, store, rules=PROTECTED, perms=None, user_age_days=400):
        self.store = store
        self.rules = rules
        self.perms = perms or {"alice": "admin"}
        self.collaborators = {"alice"}
        self.invitations: dict[int, str] = {}
        self.user_age_days = user_age_days
        self.issue = {"number": 1, "title": "KeyError in refresh_token", "body": "trace...",
                      "user": {"login": "rita", "type": "User"}, "state": "open", "comments": 0,
                      "updated_at": "2026-10-10T00:00:00Z", "created_at": "2026-10-10T00:00:00Z", "locked": False}
        self.comments: list[dict] = []
        self.log: list[tuple[str, str]] = []

    # --- reads used by ingest/effects
    def issues_since(self, repo, since):
        return [self.issue] if not since or self.issue["updated_at"] > since else []

    def get_issue(self, repo, n):
        return self.issue

    def list_issue_comments(self, repo, n):
        return self.comments

    def get_file(self, repo, path, ref=None):
        return None

    def get_repo(self, repo):
        return {"default_branch": "main", "private": False}

    def permission(self, repo, login):
        return self.perms.get(login, "read")

    def paginate(self, path, params=None, limit=1000):
        if path.endswith("/invitations"):
            return [{"id": i, "invitee": {"login": u}} for i, u in self.invitations.items()]
        return []

    def create_comment(self, repo, n, body):
        c = {"id": next(_ids), "body": body, "user": {"login": "mergemind[bot]", "type": "Bot"}}
        self.comments.append(c)
        return c

    def update_comment(self, repo, cid, body):
        for c in self.comments:
            if c["id"] == cid:
                c["body"] = body
        return {"id": cid}

    # --- generic REST used by github/access.py
    def request(self, method, path, *, point=None, params=None, json_body=None, use_etag=False):
        self.log.append((method, path))
        if path.endswith("/pulls"):
            return []
        if path.endswith("/rules/branches/main"):
            return self.rules
        if path.endswith("/protection"):
            raise GitHubError(404, "not_found", "Branch not protected")
        if path.startswith("/users/"):
            created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - self.user_age_days * 86400))
            return {"created_at": created}
        if path.endswith("/permission"):
            login = path.split("/")[-2]
            return {"role_name": self.perms.get(login, "read")}
        if "/collaborators/" in path:
            login = path.split("/")[-1]
            if method == "PUT":
                if login in self.collaborators:
                    return None
                self.invitations[next(_ids)] = login
                return {"id": 1}
            if method == "DELETE":
                self.collaborators.discard(login)
                if self.perms.get(login) == "write":  # removal drops the access we granted
                    self.perms.pop(login)
                return None
            if login in self.collaborators:
                return None
            raise GitHubError(404, "not_found", "not a collaborator")
        if "/invitations/" in path and method == "DELETE":
            self.invitations.pop(int(path.split("/")[-1]), None)
            return None
        raise AssertionError(f"unexpected {method} {path}")

    # --- helpers for tests
    def user_comment(self, login, body, assoc="NONE"):
        self.comments.append({"id": next(_ids), "body": body, "user": {"login": login, "type": "User"},
                              "author_association": assoc})
        self.issue["comments"] = len(self.comments)
        self.issue["updated_at"] = f"2026-10-10T00:{len(self.comments):02d}:00Z"

    def accept_invite(self, login):
        self.invitations = {k: v for k, v in self.invitations.items() if v != login}
        self.collaborators.add(login)
        self.perms[login] = "write"

    def bodies(self):
        return "\n\n".join(c["body"] for c in self.comments)


REPORT = TriageReport(
    issue_type="bug", summary="refresh_token raises KeyError for expired sessions.", disposition="accept",
    localization=[Region(evidence="ev_1", file="app/auth.py", start=8, end=12, symbol="TokenStore.refresh_token",
                         role="root_cause")],
    scope=[Region(evidence="ev_1", file="app/auth.py", start=8, end=12),
           Region(evidence="impact", file="app/api.py", start=1, end=5, symbol="handle_request"),
           Region(evidence="impact", file="tests/test_auth.py", start=1, end=1)],
    acceptance_criteria=["Expired sessions get a fresh token without KeyError"],
    regression_test_hint="call refresh_token with an expired session and an empty store",
    disposition_confidence=0.9)


def setup(tmp_path, policy=None, **gh_kw):
    store = Store(tmp_path / "m.db")
    gh = FakeGH(store, **gh_kw)
    policy = policy or Policy(autonomy="invite", maintainers=["alice"], access={"ttl": "10d", "checkin_at": "50%",
                                                                               "min_account_age": "30d"})
    ctx = Context(settings=Settings(work_dir=tmp_path / "work"), store=store, gh=gh, llm=None,
                  policies={REPO: policy})
    # an issue already triaged and offered (M2 output)
    run_id = store.start_run(REPO, 1, "triage", prompt_version="gate", as_of_sha="abc1234")
    store.finish_run(run_id, "ok", report_json=REPORT.model_dump_json())
    st = IssueState(repo=REPO, number=1, title="KeyError", author="rita", phase=Phase.ACCESS_OFFERED,
                    data={"report_run_id": run_id, "scope_files": sorted({r.file for r in REPORT.scope})})
    store.commit_issue(st, 0)
    # mark the issue as already seen so ingest doesn't re-open it
    from mergemind.models import Event
    store.add_event(Event(repo=REPO, number=1, source="poll", kind="issue_opened", external_id=f"{REPO}#1:opened"))
    store.conn.execute("UPDATE events SET processed_at=1")
    return ctx, gh


def phase(ctx):
    return ctx.store.load_issue(REPO, 1).phase


def test_S1_contribute_invite_accept_briefing(tmp_path):
    ctx, gh = setup(tmp_path)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.ACCESS_GRANTED
    assert list(gh.invitations.values()) == ["rita"]
    body = gh.bodies()
    assert "starter briefing for @rita" in body
    assert "https://github.com/o/r/blob/abc1234/app/auth.py#L8-L12" in body
    assert "Fixes #1" in body and "mergemind/issue-1" in body
    # invitation accepted on GitHub -> next tick moves to WORKING
    gh.accept_invite("rita")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.WORKING
    lease = ctx.store.conn.execute("SELECT status, mode FROM access_leases").fetchone()
    assert (lease["status"], lease["mode"]) == ("active", "collaborator")


def test_only_issue_author_can_contribute(tmp_path):
    ctx, gh = setup(tmp_path)
    gh.user_comment("mallory", "/contribute")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.ACCESS_OFFERED and not gh.invitations
    assert "only the issue author" in gh.bodies()


def test_unprotected_branch_blocks_access(tmp_path):
    ctx, gh = setup(tmp_path, rules=[])
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.ACCESS_OFFERED and not gh.invitations
    assert "isn't protected yet" in gh.bodies()
    assert "@alice" in gh.bodies()  # maintainer asked to add the rules


def test_young_account_is_refused(tmp_path):
    ctx, gh = setup(tmp_path, user_age_days=3)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    assert not gh.invitations and "at least 30 days old" in gh.bodies()


def test_existing_writer_is_never_revoked(tmp_path):
    ctx, gh = setup(tmp_path, perms={"alice": "admin", "rita": "write"})
    gh.collaborators.add("rita")
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.WORKING and not gh.invitations
    gh.user_comment("rita", "/mergemind withdraw")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.ACCEPTED_UNCLAIMED
    assert ("DELETE", "/repos/o/r/collaborators/rita") not in gh.log
    assert "rita" in gh.collaborators


def test_fork_mode_needs_no_invite(tmp_path):
    policy = Policy(autonomy="invite", access={"mode": "fork", "min_account_age": "0d"})
    ctx, gh = setup(tmp_path, policy=policy)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.WORKING and not gh.invitations
    assert "Fork the repository" in gh.bodies()


def test_S15_inactive_contributor_checkin_then_revoke(tmp_path):
    ctx, gh = setup(tmp_path)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    gh.accept_invite("rita")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.WORKING
    now = time.time()
    # 6 days later: check-in (50% of a 10-day lease), still WORKING
    tick(ctx, [REPO], now=now + 6 * 86400)
    assert "Checking in" in gh.bodies() and phase(ctx) == Phase.WORKING
    # 11 days later: lease expires -> access removed
    tick(ctx, [REPO], now=now + 11 * 86400)
    assert phase(ctx) == Phase.ACCEPTED_UNCLAIMED
    assert "rita" not in gh.collaborators
    assert ctx.store.conn.execute("SELECT status FROM access_leases").fetchone()[0] == "expired"


def test_declined_invitation_frees_the_issue(tmp_path):
    ctx, gh = setup(tmp_path)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    gh.invitations.clear()  # declined on GitHub
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.ACCEPTED_UNCLAIMED


def test_invited_contributor_cannot_use_maintainer_commands(tmp_path):
    ctx, gh = setup(tmp_path)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    gh.accept_invite("rita")
    tick(ctx, [REPO])
    # rita now has push access and shows up as a COLLABORATOR, but she is our contributor
    gh.user_comment("rita", "/mergemind stop", assoc="COLLABORATOR")
    tick(ctx, [REPO])
    assert not ctx.store.load_issue(REPO, 1).data.get("stopped")
    assert "write permission required" in gh.bodies()


def test_contributor_comment_counts_as_activity(tmp_path):
    ctx, gh = setup(tmp_path)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    gh.accept_invite("rita")
    tick(ctx, [REPO])
    before = ctx.store.load_issue(REPO, 1).data["last_activity"]
    time.sleep(0.01)
    gh.user_comment("rita", "Working on it, found the bug.")
    tick(ctx, [REPO])
    assert ctx.store.load_issue(REPO, 1).data["last_activity"] > before


def test_S16b_permission_denied_invite_blocks_without_retry(tmp_path):
    """403 on invite: PERMISSION_BLOCKED, the card explains, maintainers are told, nothing retries."""
    ctx, gh = setup(tmp_path)
    real = gh.request

    def deny_invite(method, path, **kw):
        if method == "PUT" and "/collaborators/" in path:
            raise GitHubError(403, "permission", "Resource not accessible by personal access token")
        return real(method, path, **kw)

    gh.request = deny_invite
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.PERMISSION_BLOCKED
    assert "lacks a GitHub permission" in gh.bodies()
    assert ctx.store.conn.execute("SELECT COUNT(*) FROM timers WHERE kind='retry_effect'").fetchone()[0] == 0


def test_author_can_contribute_again_after_withdrawing(tmp_path):
    ctx, gh = setup(tmp_path)
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    gh.accept_invite("rita")
    tick(ctx, [REPO])
    gh.user_comment("rita", "/mergemind withdraw")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.ACCEPTED_UNCLAIMED and "rita" not in gh.collaborators
    gh.user_comment("rita", "/contribute")
    tick(ctx, [REPO])
    assert phase(ctx) == Phase.ACCESS_GRANTED and list(gh.invitations.values()) == ["rita"]
