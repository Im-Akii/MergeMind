"""M6: /mergemind approve → re-verify → pinned merge → post-merge watch → revert PR."""

import itertools
import subprocess
import time

import pytest

from mergemind.code.repo_cache import RepoCache
from mergemind.engine.loop import tick
from mergemind.github.client import GitHubError
from mergemind.models import Phase
from tests.test_pr_flow import PRGH, REPO, FakeCache, branch_with, git, make_env, phase

_ids = itertools.count(9000)


class MergeGH(PRGH):
    def __init__(self, store, repo_path):
        super().__init__(store, repo_path)
        self.base_tip = self.base
        self.checks_by_sha: dict[str, list] = {}
        self.reviews: list[dict] = []
        self.merges: list[dict] = []
        self.created_prs: list[dict] = []
        self.compare: dict[tuple[str, str], dict] = {}
        self.merge_error: GitHubError | None = None
        self.on_update_branch = None
        self.main_statuses: list[dict] = []

    def request(self, method, path, *, point=None, params=None, json_body=None, use_etag=False):
        if path.endswith("/branches/main") and method == "GET":
            return {"commit": {"sha": self.base_tip}}
        if path.endswith("/update-branch"):
            assert json_body["expected_head_sha"] == self.pr["head"]["sha"]
            self.on_update_branch()
            return {"message": "Updating pull request branch."}
        if path.endswith("/reviews") and method == "POST":
            self.reviews.append(json_body)
            return {"id": 1}
        if path.endswith("/merge") and method == "PUT":
            if self.merge_error:
                raise self.merge_error
            self.merges.append(json_body)
            return {"sha": "m3rg3d0000000000000000000000000000000000", "merged": True}
        if "/compare/" in path:
            a, b = path.split("/compare/")[1].split("...")
            return self.compare[(a, b)]
        if path.endswith("/status") and method == "GET":
            return {"statuses": self.main_statuses}
        if path.endswith("/pulls") and method == "POST":
            self.created_prs.append(json_body)
            return {"number": 99}
        return super().request(method, path, point=point, params=params, json_body=json_body, use_etag=use_etag)


class RecordingCache(FakeCache):
    reverts: list = []

    def revert_and_push(self, repo, sha, base, branch):
        RecordingCache.reverts.append((sha, base, branch))


@pytest.fixture()
def m(tmp_path):
    RecordingCache.reverts = []
    return make_env(tmp_path, gh_cls=MergeGH, autonomy="merge", cache_cls=RecordingCache)


def comment(e, login, body, *, on_pr=True, assoc="OWNER"):
    target = e.gh.pr_comments if on_pr else e.gh.comments
    target.append({"id": next(_ids), "body": body, "user": {"login": login, "type": "User"},
                   "author_association": assoc})
    if on_pr:
        e.gh.pr["updated_at"] = f"2026-10-11T00:{next(_ids) % 60:02d}:00Z"
    else:
        e.gh.issue["comments"] = len(e.gh.comments)
        e.gh.issue["updated_at"] = f"2026-10-11T00:{next(_ids) % 60:02d}:00Z"


def ready(e) -> str:
    head = branch_with(e.repo, "good", "KIT_FIX.patch")
    e.gh.open_pr(head)
    e.gh.set_ci("success", "success")
    tick(e.ctx, [REPO])
    assert phase(e) == Phase.READY_FOR_MAINTAINER
    return head


def issue_text(e):
    return "\n\n".join(c["body"] for c in e.gh.comments)


# ------------------------------------------------------------------ happy path


def test_S6_approve_merges_pinned_commit_then_done(m):
    head = ready(m)
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    assert phase(m) == Phase.POST_MERGE_WATCH
    assert m.gh.merges == [{"sha": head, "merge_method": "squash", "commit_title": "Make coupons case-insensitive (#7)",
                            "commit_message": f"Fixes #1\n\nVerified by MergeMind at {head[:10]}; approved by alice."}]
    assert m.gh.reviews[0]["event"] == "APPROVE" and m.gh.reviews[0]["commit_id"] == head
    assert "Merged after verification" in issue_text(m)
    # default-branch CI on the merge commit goes green
    m.gh.checks_by_sha["m3rg3d0000000000000000000000000000000000"] = [
        {"id": 1, "name": "tests", "status": "completed", "conclusion": "success"}]
    tick(m.ctx, [REPO], now=time.time() + 31 * 60)
    assert phase(m) == Phase.DONE
    assert "rita" not in m.gh.collaborators


def test_no_default_branch_ci_still_finishes(m):
    ready(m)
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    m.gh.checks_by_sha["m3rg3d0000000000000000000000000000000000"] = []  # no CI on the default branch
    t = time.time() + 31 * 60
    for i in range(4):
        tick(m.ctx, [REPO], now=t + i * 120)
    assert phase(m) == Phase.DONE
    assert m.ctx.store.load_issue(REPO, 1).data["post_merge_ci"] == "none"


# --------------------------------------------------------------- who/when


def test_contributor_cannot_approve_own_fix(m):
    ready(m)
    comment(m, "rita", "/mergemind approve", assoc="COLLABORATOR")
    tick(m.ctx, [REPO])
    assert phase(m) == Phase.READY_FOR_MAINTAINER and m.gh.merges == []


def test_invite_autonomy_cannot_merge(tmp_path):
    e = make_env(tmp_path, gh_cls=MergeGH, autonomy="invite", cache_cls=RecordingCache)
    ready(e)
    comment(e, "alice", "/mergemind approve")
    tick(e.ctx, [REPO])
    assert phase(e) == Phase.READY_FOR_MAINTAINER and e.gh.merges == []


def test_approve_before_ready_is_refused(m):
    head = branch_with(m.repo, "nt", "KIT_FIX_NO_TEST.patch")
    m.gh.open_pr(head)
    m.gh.set_ci("success", None)
    tick(m.ctx, [REPO])
    assert phase(m) == Phase.CHANGES_REQUESTED
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    assert m.gh.merges == [] and "not ready" in m.gh.pr_text() + issue_text(m)


# ------------------------------------------------------------- re-verification


def test_S11_push_after_approval_voids_it(m):
    ready(m)
    head2 = branch_with(m.repo, "good2", "KIT_FIX.patch", extra={"shopcart/extra.py": "X = 1\n"})
    m.gh.pr["head"]["sha"] = head2  # contributor pushes...
    # ...as the maintainer approves (on the issue thread, which is read before the new push is seen;
    # approving on the PR thread is refused outright because the push is seen first)
    comment(m, "alice", "/mergemind approve", on_pr=False)
    tick(m.ctx, [REPO])
    assert m.gh.merges == []
    assert "approval no longer applies" in issue_text(m)
    # the new commit was re-verified; it needs a fresh approval
    st = m.ctx.store.load_issue(REPO, 1)
    assert st.phase == Phase.READY_FOR_MAINTAINER and st.approved_by is None
    assert st.data["verified_head_sha"] == head2


def _move_base(m):
    """Someone merges another change into main after verification."""
    git(m.repo, "checkout", "-q", "main")
    (m.repo / "README.md").write_text("# shopcart\n\nUpdated docs.\n")
    git(m.repo, "commit", "-qam", "docs")
    new_base = git(m.repo, "rev-parse", "HEAD")
    git(m.repo, "update-ref", "refs/remotes/origin/main", new_base)
    m.gh.base_tip = new_base
    return new_base


def _github_update_branch(m, old_head, new_base, committer="web-flow", extra_commit=False):
    def update():
        git(m.repo, "checkout", "-q", "good")
        git(m.repo, "merge", "-q", "--no-edit", "main")
        new_head = git(m.repo, "rev-parse", "HEAD")
        git(m.repo, "checkout", "-q", "main")
        commits = [{"sha": new_head, "parents": [{"sha": old_head}, {"sha": new_base}], "committer": {"login": committer}}]
        if extra_commit:
            commits.insert(0, {"sha": "x", "parents": [{"sha": old_head}], "committer": {"login": "Im-Akii"}})
        m.gh.compare[(old_head, new_head)] = {"commits": commits}
        m.gh.compare[(new_base, "main")] = {"status": "identical"}
        m.gh.pr["head"]["sha"] = new_head
        m.gh.checks_by_sha[new_head] = [
            {"id": 21, "name": "tests", "status": "completed", "conclusion": "success", "started_at": "2"},
            {"id": 22, "name": "regression", "status": "completed", "conclusion": "success", "started_at": "2"}]
        m.gh.pr["updated_at"] = "2026-10-11T09:00:00Z"
        m.new_head = new_head
    return update


def test_S12_base_moved_updates_branch_and_keeps_approval(m):
    head = ready(m)
    new_base = _move_base(m)
    m.gh.on_update_branch = _github_update_branch(m, head, new_base)
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])  # approve -> base moved -> update-branch requested
    assert m.gh.merges == [] and phase(m) == Phase.PR_ANALYSING
    tick(m.ctx, [REPO])  # new head analysed -> still ready -> approval carried -> merge
    assert m.gh.merges and m.gh.merges[0]["sha"] == m.new_head
    assert phase(m) == Phase.POST_MERGE_WATCH
    assert "approval no longer applies" not in issue_text(m)  # no second approval was needed
    assert m.gh.reviews[-1]["commit_id"] == m.new_head


def test_update_with_extra_commits_needs_a_human(m):
    head = ready(m)
    new_base = _move_base(m)
    m.gh.on_update_branch = _github_update_branch(m, head, new_base, extra_commit=True)
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    tick(m.ctx, [REPO])
    assert m.gh.merges == []
    assert phase(m) == Phase.SAFETY_HOLD
    assert "couldn't merge" in issue_text(m)


def test_update_merge_not_made_by_github_is_rejected(m):
    head = ready(m)
    new_base = _move_base(m)
    m.gh.on_update_branch = _github_update_branch(m, head, new_base, committer="Im-Akii")
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    tick(m.ctx, [REPO])
    assert m.gh.merges == [] and phase(m) == Phase.SAFETY_HOLD


def test_github_refusing_the_merge_returns_to_maintainer(m):
    ready(m)
    m.gh.merge_error = GitHubError(405, "conflict", "At least 1 approving review is required")
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    assert phase(m) == Phase.READY_FOR_MAINTAINER
    assert "GitHub refused the merge" in issue_text(m)


# ------------------------------------------------------------------ overrides


def test_unclear_criteria_hold_can_be_overridden(m):
    m.nemo.criteria_met = "unclear"
    head = branch_with(m.repo, "good", "KIT_FIX.patch")
    m.gh.open_pr(head)
    m.gh.set_ci("success", "success")
    tick(m.ctx, [REPO])
    assert phase(m) == Phase.SAFETY_HOLD
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    assert m.gh.merges and phase(m) == Phase.POST_MERGE_WATCH


def test_safety_hold_cannot_be_overridden(m):
    head = branch_with(m.repo, "sneaky", "KIT_FIX.patch", extra={".github/workflows/x.yml": "name: x\n"})
    m.gh.open_pr(head)
    m.gh.set_ci("success", "success")
    tick(m.ctx, [REPO])
    assert phase(m) == Phase.SAFETY_HOLD
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    assert m.gh.merges == [] and "can't be overridden" in issue_text(m) + m.gh.pr_text()


# ------------------------------------------------------------------- revert


def test_S14_red_default_branch_opens_revert_pr(m):
    ready(m)
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    m.gh.checks_by_sha["m3rg3d0000000000000000000000000000000000"] = [
        {"id": 1, "name": "tests", "status": "completed", "conclusion": "failure"}]
    tick(m.ctx, [REPO], now=time.time() + 31 * 60)
    assert phase(m) == Phase.REVERT_PROPOSED
    assert RecordingCache.reverts == [("m3rg3d0000000000000000000000000000000000", "main", "mergemind/revert-m3rg3d00")]
    assert m.gh.created_prs[0]["head"] == "mergemind/revert-m3rg3d00"
    assert "Revert proposed in #99" in issue_text(m)


def test_revert_and_push_real_git(tmp_path):
    """The real RepoCache revert: branch from origin/main, revert, push; main itself untouched."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "a.txt").write_text("v1\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "v1")
    (seed / "a.txt").write_text("v2 (bad)\n")
    git(seed, "commit", "-qam", "bad change")
    bad = git(seed, "rev-parse", "HEAD")
    git(seed, "push", "-q", str(origin), "main")

    cache = RepoCache(tmp_path / "work")
    dest = cache.path("o/r")
    git(tmp_path, "clone", "-q", str(origin), str(dest))
    cache.revert_and_push("o/r", bad, "main", "mergemind/revert-test")

    branches = git(origin, "branch", "--list")
    assert "mergemind/revert-test" in branches
    assert git(origin, "show", "mergemind/revert-test:a.txt") == "v1"
    assert git(origin, "show", "main:a.txt") == "v2 (bad)"


def test_verdict_without_recorded_base_asks_github(m):
    """Verdicts from before base_sha was recorded: a PR behind the base is updated, not merged stale."""
    head = ready(m)
    rid = m.ctx.store.load_issue(REPO, 1).data["verdict_run_id"]
    row = m.ctx.store.conn.execute("SELECT verdict_json FROM runs WHERE id=?", (rid,)).fetchone()
    import json as _json
    v = _json.loads(row[0])
    v.pop("base_sha")
    m.ctx.store.conn.execute("UPDATE runs SET verdict_json=? WHERE id=?", (_json.dumps(v), rid))
    new_base = _move_base(m)
    m.gh.compare[(head, new_base)] = {"ahead_by": 1}
    m.gh.on_update_branch = _github_update_branch(m, head, new_base)
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    assert m.gh.merges == [] and phase(m) == Phase.PR_ANALYSING  # updated first
    tick(m.ctx, [REPO])
    assert m.gh.merges and m.gh.merges[0]["sha"] == m.new_head


def test_S16a_rate_limited_merge_retries_at_reset(m):
    """429 on merge: no busy-waiting; a durable timer retries at the reset time and the merge succeeds."""
    ready(m)
    reset = time.time() + 120
    real_request = m.gh.request
    calls = {"merge": 0}

    def flaky(method, path, **kw):
        if path.endswith("/merge") and method == "PUT":
            calls["merge"] += 1
            if calls["merge"] == 1:
                raise GitHubError(429, "rate_limited", "API rate limit exceeded", reset_at=reset)
        return real_request(method, path, **kw)

    m.gh.request = flaky
    comment(m, "alice", "/mergemind approve")
    tick(m.ctx, [REPO])
    assert phase(m) == Phase.MERGING and m.gh.merges == []
    timer = m.ctx.store.conn.execute("SELECT kind, due_at FROM timers WHERE kind='retry_effect'").fetchone()
    assert timer and abs(timer["due_at"] - reset) < 1
    tick(m.ctx, [REPO], now=reset + 1)
    assert m.gh.merges and phase(m) == Phase.POST_MERGE_WATCH
