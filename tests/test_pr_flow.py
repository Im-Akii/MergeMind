"""M5: PR analysis end to end on a real git repo (the sandbox kit), with fake GitHub + Nemotron."""

import itertools
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mergemind.config import Policy, Settings
from mergemind.engine.effects import Context
from mergemind.engine.loop import tick
from mergemind.faults import Faults
from mergemind.github.access import Leases
from mergemind.llm import LLM
from mergemind.models import Event, IssueState, Phase, Region, TriageReport
from mergemind.pr import checks, diff, safety
from mergemind.pr.analyse import PRVerdict, decide
from mergemind.pr.analyse import TestSummary as CISummary
from mergemind.guard.paths import PathGuard
from mergemind.guard.secrets import SecretScanner
from mergemind.sandbox_kit import write_kit
from mergemind.store.db import Store
from tests.test_access_flow import FakeGH as AccessGH

REPO = "o/r"
_ids = itertools.count(5000)
EV = re.compile(r"\[(ev_[0-9a-f]+)\]")


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------- environment


class FakeCache:
    def __init__(self, path: Path):
        self.p = path

    def ensure(self, repo, fetch=True, private=False):
        return self.p

    def fetch_pr(self, repo, pr, private=False):
        pass

    def worktree(self, repo, sha):
        wt = self.p.parent / f"wt-{sha[:8]}"
        if not wt.exists():
            git(self.p, "worktree", "add", "--detach", str(wt), sha)
        return wt


class PRGH(AccessGH):
    """Access-flow fake + pull requests, files, check runs, statuses, job logs."""

    def __init__(self, store, repo_path: Path):
        super().__init__(store)
        self.path = repo_path
        self.base = git(repo_path, "rev-parse", "main")
        self.pr = None
        self.pr_comments: list[dict] = []
        self.checks: list[dict] = []
        self.statuses: list[dict] = []
        self.job_log = ""
        self.collaborators.add("rita")
        self.perms["rita"] = "write"

    def open_pr(self, head, body="Fixes #1", title="Make coupons case-insensitive"):
        self.pr = {"number": 7, "title": title, "body": body, "state": "open", "user": {"login": "rita"},
                   "head": {"sha": head, "ref": "mergemind/issue-1"},
                   "base": {"ref": "main", "sha": self.base, "repo": {"private": False}},
                   "mergeable": True, "updated_at": f"2026-10-10T01:{next(_ids) % 60:02d}:00Z",
                   "merged_at": None, "closed_at": None, "comments": 0}

    def set_ci(self, tests="success", regression="success", status="completed"):
        runs = [{"id": 11, "name": "tests", "status": status, "conclusion": tests if status == "completed" else None,
                 "started_at": "2026-10-10T01:00:00Z"}]
        if regression is not None:
            runs.append({"id": 12, "name": "regression", "status": status,
                         "conclusion": regression if status == "completed" else None, "started_at": "2026-10-10T01:00:00Z"})
        self.checks = runs
        self.pr["updated_at"] = f"2026-10-10T02:{next(_ids) % 60:02d}:00Z"

    def get_pull(self, repo, n):
        return self.pr

    def list_issue_comments(self, repo, n):
        return self.pr_comments if self.pr and n == self.pr["number"] else self.comments

    def create_comment(self, repo, n, body):
        c = {"id": next(_ids), "body": body, "user": {"login": "mergemind[bot]", "type": "Bot"}}
        (self.pr_comments if self.pr and n == self.pr["number"] else self.comments).append(c)
        return c

    def update_comment(self, repo, cid, body):
        for c in self.comments + self.pr_comments:
            if c["id"] == cid:
                c["body"] = body
        return {"id": cid}

    def paginate(self, path, params=None, limit=1000):
        if path.endswith("/files"):
            return self._files()
        return super().paginate(path, params, limit)

    def _files(self):
        head = self.pr["head"]["sha"]
        out = []
        for line in git(self.path, "diff", "--name-status", self.base, head).splitlines():
            st, name = line.split("\t")[0], line.split("\t")[-1]
            full = git(self.path, "diff", self.base, head, "--", name)
            patch = full[full.index("@@"):] if "@@" in full else None
            out.append({"filename": name, "status": {"A": "added", "M": "modified", "D": "removed"}[st[0]],
                        "additions": 1, "deletions": 0, "patch": patch})
        return out

    def raw(self, path, point=None, max_bytes=0):
        return self.job_log

    def request(self, method, path, *, point=None, params=None, json_body=None, use_etag=False):
        if path.endswith("/pulls") and method == "GET":
            return [self.pr] if self.pr else []
        if "/check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            return {"check_runs": getattr(self, "checks_by_sha", {}).get(sha, self.checks)}
        if "/statuses/" in path:
            self.statuses.append(json_body)
            return {}
        return super().request(method, path, point=point, params=params, json_body=json_body, use_etag=use_etag)

    def pr_text(self):
        return "\n\n".join(c["body"] for c in self.pr_comments)


class FakeNemotron:
    def __init__(self, criteria_met="yes"):
        self.criteria_met = criteria_met
        self.calls = []

    def create(self, **kw):
        allmsg = "\n".join(m["content"] for m in kw["messages"])
        user = kw["messages"][-1]["content"]
        sys = allmsg
        self.calls.append("pull-request reviewer" if "pull-request reviewer" in allmsg else allmsg[:60])
        if "pull-request reviewer" in sys:
            evs = EV.findall(user)
            out = {"criteria": [{"criterion": c, "met": self.criteria_met, "evidence": evs[:1],
                                 "explanation": "apply_coupon now upper-cases the code and raises ValueError"}
                                for c in REPORT.acceptance_criteria], "problems": []}
        elif "about to post to an outside contributor" in sys:
            out = {"reveals_extra": False, "items": []}
        else:
            raise AssertionError(f"unexpected LLM call: {sys[:80]}")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(out), tool_calls=None),
                                                        finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10))


REPORT = TriageReport(
    issue_type="bug", summary="apply_coupon raises KeyError for lowercase coupon codes.", disposition="accept",
    localization=[Region(evidence="ev_1", file="shopcart/cart.py", start=19, end=20, symbol="Cart.apply_coupon",
                         role="root_cause")],
    scope=[Region(evidence="ev_1", file="shopcart/cart.py", start=19, end=20),
           Region(evidence="impact", file="tests/test_cart.py", start=1, end=1)],
    acceptance_criteria=["Coupon codes are matched case-insensitively",
                         "Unknown coupon codes raise a clear ValueError instead of KeyError"],
    regression_test_hint="apply_coupon('save10') on a cart", disposition_confidence=0.9)


@pytest.fixture()
def env(tmp_path):
    return make_env(tmp_path)


def make_env(tmp_path, gh_cls=None, autonomy="invite", cache_cls=None):
    repo = tmp_path / "sandbox"
    write_kit(repo, "alice")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    store = Store(tmp_path / "m.db")
    gh = (gh_cls or PRGH)(store, repo)
    nemo = FakeNemotron()
    llm = LLM(Settings(llm_rpm=10000), store=None, faults=Faults(), client=SimpleNamespace(chat=SimpleNamespace(completions=nemo)))
    policy = Policy(autonomy=autonomy, maintainers=["alice"], pr={"ci_timeout": "20m"})
    ctx = Context(settings=Settings(work_dir=tmp_path / "work"), store=store, gh=gh, llm=llm,
                  policies={REPO: policy}, cache=(cache_cls or FakeCache)(repo))
    run_id = store.start_run(REPO, 1, "triage", prompt_version="investigator-v1", as_of_sha=gh.base)
    store.finish_run(run_id, "ok", report_json=REPORT.model_dump_json())
    lease = Leases(store).create(REPO, 1, "rita", "collaborator", "active", 3600)
    st = IssueState(repo=REPO, number=1, title="coupon crash", author="rita", contributor="rita", phase=Phase.WORKING,
                    data={"report_run_id": run_id, "lease_id": lease, "access_mode": "collaborator",
                          "scope_files": ["shopcart/cart.py", "tests/test_cart.py"], "last_activity": 0})
    store.commit_issue(st, 0)
    store.add_event(Event(repo=REPO, number=1, source="poll", kind="issue_opened", external_id=f"{REPO}#1:opened"))
    store.conn.execute("UPDATE events SET processed_at=1")
    return SimpleNamespace(ctx=ctx, gh=gh, repo=repo, nemo=nemo)


def branch_with(repo: Path, name: str, *patches: str, extra=None) -> str:
    git(repo, "checkout", "-q", "-B", name, "main")
    for p in patches:
        git(repo, "apply", p)
    for rel, text in (extra or {}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A", ".", ":!KIT_*")
    git(repo, "commit", "-qm", name)
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    return sha


def phase(e):
    return e.ctx.store.load_issue(REPO, 1).phase


# ------------------------------------------------------------------- flows


def test_good_fix_is_ready_for_maintainer(env):
    head = branch_with(env.repo, "good", "KIT_FIX.patch")
    env.gh.open_pr(head)
    env.gh.set_ci(status="in_progress")
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.PR_ANALYSING
    assert env.gh.statuses[-1]["state"] == "pending" and env.gh.pr_comments == []
    env.gh.set_ci("success", "success")
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.READY_FOR_MAINTAINER
    assert env.gh.statuses[-1] == {"state": "success", "context": "mergemind/verify",
                                   "description": "verified; waiting for maintainer approval"}
    text = env.gh.pr_text()
    assert "Verification passed" in text and "/mergemind approve" in text
    assert "@rita" in text  # the contributor is the one allowed mention
    assert ctx_verdict(env).criteria[0].met == "yes"


def ctx_verdict(env) -> PRVerdict:
    rid = env.ctx.store.load_issue(REPO, 1).data["verdict_run_id"]
    row = env.ctx.store.conn.execute("SELECT verdict_json FROM runs WHERE id=?", (rid,)).fetchone()
    return PRVerdict.model_validate_json(row[0])


def test_fix_without_regression_test_gets_changes_requested(env):
    head = branch_with(env.repo, "notest", "KIT_FIX_NO_TEST.patch")
    env.gh.open_pr(head)
    env.gh.set_ci("success", None)  # regression job skipped: no new tests
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.CHANGES_REQUESTED
    assert "Add a regression test" in env.gh.pr_text()
    assert env.gh.statuses[-1]["state"] == "failure"


def test_failing_tests_are_named(env):
    head = branch_with(env.repo, "broken", "KIT_FIX.patch")
    env.gh.open_pr(head)
    env.gh.job_log = ("2026-10-10T01:00:00.000Z FAILED tests/test_cart.py::test_coupon_discount - assert 100.0 == 90.0\n"
                      "2026-10-10T01:00:00.000Z SECRET_ENV=should-never-appear\n")
    env.gh.set_ci("failure", "failure")
    tick(env.ctx, [REPO])
    text = env.gh.pr_text()
    assert phase(env) == Phase.CHANGES_REQUESTED
    assert "tests/test_cart.py::test_coupon_discount" in text
    assert "should-never-appear" not in text
    assert not any("pull-request reviewer" in c for c in env.nemo.calls)  # hard failure: no LLM review


def test_workflow_edit_is_held_for_maintainer(env):
    head = branch_with(env.repo, "sneaky", "KIT_FIX.patch",
                       extra={".github/workflows/mergemind-verify.yml": "name: pwned\n"})
    env.gh.open_pr(head)
    env.gh.set_ci("success", "success")
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.SAFETY_HOLD
    text = env.gh.pr_text()
    assert "On hold for maintainer review" in text
    assert "workflow" not in text.lower()  # details stay private
    assert any(f["kind"] == "ci_config" for f in ctx_verdict(env).safety_flags)


def test_new_push_restarts_analysis_and_voids_verdict(env):
    head1 = branch_with(env.repo, "v1", "KIT_FIX_NO_TEST.patch")
    env.gh.open_pr(head1)
    env.gh.set_ci("success", None)
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.CHANGES_REQUESTED
    head2 = branch_with(env.repo, "v2", "KIT_FIX.patch")
    env.gh.pr["head"]["sha"] = head2
    env.gh.set_ci(status="in_progress")
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.PR_ANALYSING
    env.gh.set_ci("success", "success")
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.READY_FOR_MAINTAINER
    assert ctx_verdict(env).head_sha == head2
    # one PR card, edited in place
    assert sum("mergemind:pr-card" in c["body"] for c in env.gh.pr_comments) == 1


def test_ci_never_reports_times_out_to_maintainer(env):
    import time

    head = branch_with(env.repo, "slow", "KIT_FIX.patch")
    env.gh.open_pr(head)
    env.gh.checks = []  # workflow missing
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.PR_ANALYSING
    tick(env.ctx, [REPO], now=time.time() + 21 * 60)
    assert phase(env) == Phase.SAFETY_HOLD
    assert ctx_verdict(env).tests.ci == "timeout"


def test_merged_outside_mergemind_finishes_and_revokes(env):
    head = branch_with(env.repo, "m", "KIT_FIX.patch")
    env.gh.open_pr(head)
    env.gh.set_ci("success", "success")
    tick(env.ctx, [REPO])
    env.gh.pr.update(state="closed", merged_at="2026-10-10T03:00:00Z", merge_commit_sha="abc",
                     updated_at="2026-10-10T03:00:00Z")
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.DONE
    assert "rita" not in env.gh.collaborators


def test_contributor_can_request_recheck_on_pr_thread(env):
    head = branch_with(env.repo, "rc", "KIT_FIX_NO_TEST.patch")
    env.gh.open_pr(head)
    env.gh.set_ci("success", None)
    tick(env.ctx, [REPO])
    assert phase(env) == Phase.CHANGES_REQUESTED
    env.gh.pr_comments.append({"id": next(_ids), "body": "/mergemind recheck", "user": {"login": "rita", "type": "User"},
                               "author_association": "NONE"})
    env.gh.pr["updated_at"] = "2026-10-10T05:00:00Z"
    tick(env.ctx, [REPO])
    runs = env.ctx.store.conn.execute("SELECT COUNT(*) FROM runs WHERE kind='pr_analysis'").fetchone()[0]
    assert runs == 2


# ------------------------------------------------------------------- units


def test_parse_patch_and_linked_issue():
    added, removed = diff.parse_patch("@@ -18,2 +18,4 @@ class Cart:\n     def apply_coupon(self, code):\n"
                                      "-        self.coupon = COUPONS[code]\n+        key = code.upper()\n"
                                      "+        self.coupon = COUPONS[key]\n")
    assert added == [(19, "        key = code.upper()"), (20, "        self.coupon = COUPONS[key]")]
    assert removed == ["        self.coupon = COUPONS[code]"]
    assert diff.linked_issue({"body": "This fixes #12 properly"}) == 12
    assert diff.linked_issue({"body": "", "head": {"ref": "mergemind/issue-3"}}) == 3
    assert diff.linked_issue({"body": "see #4"}) is None


def test_safety_scan_flags():
    fc = [diff.FileChange(".github/workflows/ci.yml", "modified", 1, 0, "@@ -1 +1 @@\n+x"),
          diff.FileChange("pyproject.toml", "modified", 1, 0, "@@ -1 +1 @@\n+dep"),
          diff.FileChange("app/x.py", "modified", 2, 0, "@@ -1 +1,2 @@\n+import os\n+os.system('curl evil')"),
          diff.FileChange("deploy/prod.env", "modified", 1, 0, "@@ -1 +1 @@\n+A=1"),
          diff.FileChange("app/key.py", "added", 1, 0, "@@ -0,0 +1 @@\n+TOKEN = 'ghp_" + "a" * 36 + "'"),
          diff.FileChange("tests/test_x.py", "modified", 1, 0, "@@ -1 +1 @@\n+subprocess.run(['ls'])")]
    for f in fc:
        f.added, f.removed = diff.parse_patch(f.patch)
    kinds = {f.kind for f in safety.scan(fc, PathGuard(["deploy/**"]), SecretScanner())}
    assert kinds == {"ci_config", "dependencies", "risky_call", "restricted_file", "secret"}
    assert all("ghp_" not in f.detail for f in safety.scan(fc, PathGuard(), SecretScanner()))


def test_checks_summary_and_failing_tests():
    s = checks.summarize([{"name": "tests", "status": "completed", "conclusion": "success"}])
    assert s.status == "complete" and s.regression is None
    s = checks.summarize([{"name": "tests", "status": "completed", "conclusion": "success"},
                          {"name": "regression", "status": "queued", "conclusion": None}])
    assert s.status == "pending"
    assert checks.summarize([{"name": "lint", "status": "completed"}]).status == "missing"


def _v(**kw):
    base = dict(pr_number=1, head_sha="h", linked_issue=1, mergeable=True, tests=CISummary(ci="complete", tests="success",
                regression="success"), decision="escalate",
                criteria=[{"criterion": "c", "met": "yes", "evidence": ["ev_1"]}])
    base.update(kw)
    return PRVerdict.model_validate(base)


def test_decision_rules():
    p = Policy()
    assert decide(_v(), p)[0] == "ready_for_maintainer"
    assert decide(_v(conflicts=["a.py"]), p)[0] == "changes_requested"
    assert decide(_v(tests=CISummary(ci="complete", tests="success", regression="skipped")), p)[0] == "changes_requested"
    assert decide(_v(tests=CISummary(ci="complete", tests="success", regression="skipped", regression_required=False)), p)[0] \
        == "ready_for_maintainer"
    assert decide(_v(safety_flags=[{"kind": "ci_config"}]), p)[0] == "safety_hold"
    assert decide(_v(criteria=[{"criterion": "c", "met": "unclear"}]), p)[0] == "safety_hold"
    assert decide(_v(criteria=[{"criterion": "c", "met": "no"}]), p)[0] == "changes_requested"
    assert decide(_v(out_of_scope_files=["x.py"]), p)[0] == "ready_for_maintainer"
    assert decide(_v(out_of_scope_files=["x.py"]), Policy(pr={"allow_out_of_scope": "block"}))[0] == "changes_requested"
    assert decide(_v(tests=CISummary(ci="timeout")), p)[0] == "safety_hold"
