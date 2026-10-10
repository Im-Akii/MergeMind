"""PR analysis (buildplan-v2 §7.5): link → mergeability → scope → impact → tests (from CI) →
safety → fix check → rule-based decision. Runs on the PR head; never executes PR code."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from mergemind.agents.ledger import Ledger
from mergemind.code.graph import code_intel, is_doc, is_test
from mergemind.code.repo_cache import RepoCache, git
from mergemind.config import Policy, Settings
from mergemind.github.client import GitHub
from mergemind.guard.paths import PathGuard
from mergemind.guard.secrets import SecretScanner
from mergemind.llm import LLM, LLMError, Usage
from mergemind.models import Claim, TriageReport
from mergemind.pr import checks, diff, safety
from mergemind.pr.review import CriterionResult, review
from mergemind.store.db import Store
from mergemind.tracing import span

log = logging.getLogger(__name__)

Decision = Literal["changes_requested", "safety_hold", "ready_for_maintainer", "escalate"]


class TestSummary(BaseModel):
    ci: str  # complete | missing | timeout
    tests: str | None = None
    regression: str | None = None
    regression_required: bool = True
    failing: list[str] = Field(default_factory=list)


class PRVerdict(BaseModel):
    pr_number: int
    head_sha: str
    base_ref: str = "main"
    base_sha: str | None = None  # base branch tip the verification ran against
    linked_issue: int | None
    mergeable: bool
    conflicts: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    out_of_scope_files: list[str] = Field(default_factory=list)
    affected: list[str] = Field(default_factory=list)  # "file::symbol" callers of changed code
    tests: TestSummary
    safety_flags: list[dict[str, Any]] = Field(default_factory=list)
    criteria: list[CriterionResult] = Field(default_factory=list)
    issues_found: list[Claim] = Field(default_factory=list)
    decision: Decision
    reasons: list[str] = Field(default_factory=list)
    # a maintainer may approve despite this hold (only "the review couldn't confirm a criterion");
    # safety flags and missing CI can never be overridden by command
    overridable: bool = False


@dataclass
class Waiting:
    head_sha: str
    reason: str = "waiting for mergemind-verify"


@dataclass
class Analysis:
    verdict: PRVerdict
    run_id: int
    usage: Usage = field(default_factory=Usage)


def decide(v: PRVerdict, policy: Policy) -> tuple[Decision, list[str]]:
    """Code, not the LLM, decides. `ready_for_maintainer` needs every check to pass."""
    reasons: list[str] = []
    if v.safety_flags:
        return "safety_hold", [f"{len(v.safety_flags)} safety flag(s) need a maintainer"]
    if v.tests.ci in ("missing", "timeout"):
        return "safety_hold", ["the mergemind-verify workflow did not report results"]
    hard = []
    if not v.mergeable or v.conflicts:
        hard.append("merge conflicts with the base branch")
    if v.tests.tests != "success":
        hard.append("tests are failing")
    if v.tests.regression_required and v.tests.regression != "success":
        hard.append("no regression test that fails before the fix and passes after it"
                    if v.tests.regression in (None, "skipped") else "the regression test does not fail on the base branch")
    if v.out_of_scope_files and policy.pr.allow_out_of_scope == "block":
        hard.append("changes outside the issue's scope")
    if any(c.met == "no" for c in v.criteria):
        hard.append("acceptance criteria not met")
    if v.issues_found:
        hard.append("problems found in the change")
    if hard:
        return "changes_requested", hard
    if not v.criteria or any(c.met == "unclear" for c in v.criteria):
        return "safety_hold", ["a maintainer needs to judge criteria the review couldn't confirm"]
    if v.out_of_scope_files:
        reasons.append("touches files outside the issue's scope (flagged for the maintainer)")
    return "ready_for_maintainer", reasons


@span("WORKFLOW", "pr_run")
def analyse_pr(
    *,
    settings: Settings,
    store: Store,
    gh: GitHub,
    llm: LLM | None,
    policy: Policy,
    repo: str,
    issue_number: int,
    pr_number: int,
    report: TriageReport | None,
    scope_files: list[str],
    cache: RepoCache | None = None,
    ci_timed_out: bool = False,
) -> Analysis | Waiting:
    pr = gh.get_pull(repo, pr_number)
    head_sha = pr["head"]["sha"]
    base_ref = pr["base"]["ref"]

    ci = checks.summarize(checks.check_runs(gh, repo, head_sha))
    if ci.status in ("pending", "missing") and not ci_timed_out:
        return Waiting(head_sha)

    cache = cache or RepoCache(settings.work_dir, settings.github_token)
    private = bool(pr["base"]["repo"].get("private"))
    path = cache.ensure(repo, fetch=True, private=private)
    cache.fetch_pr(repo, pr_number, private=private)
    files = diff.fetch_files(gh, repo, pr_number)
    worktree = cache.worktree(repo, head_sha)

    run_id = store.start_run(repo, issue_number, "pr_analysis", as_of_sha=head_sha, prompt_version="pr-review-v1",
                             model=settings.llm_model_investigator)
    ledger = Ledger(store, run_id, head_sha)
    usage = Usage()
    guard = PathGuard(policy.guard.sensitive_paths)

    # mergeability
    base_sha = git(path, "rev-parse", f"origin/{base_ref}", check=False).strip() or None
    conflicts = diff.merge_conflicts(path, f"origin/{base_ref}", head_sha)
    mergeable = pr.get("mergeable") is not False and not conflicts

    # scope: test, doc and changelog edits are always fine
    scope = set(scope_files)
    changed = [f.path for f in files]
    out_of_scope = [p for p in changed if p not in scope and not is_test(p) and not is_doc(p)]

    # impact of the changed code
    intel = code_intel(worktree)
    affected: list[str] = []
    for file, s in diff.changed_symbols(worktree, files)[:5]:
        imp = intel.impact(s.name, file)
        affected += [f"{c.file}::{c.symbol}" for c in imp.callers[:8]]
    affected = list(dict.fromkeys(affected))[:20]

    # tests (results from the untrusted workflow)
    failing = checks.failing_tests(gh, repo, ci.tests_job_id) if ci.tests == "failure" else []
    regression_required = policy.pr.require_regression_test and (report is None or report.issue_type == "bug")
    tests = TestSummary(ci="timeout" if ci_timed_out and ci.status != "complete" else ci.status,
                        tests=ci.tests, regression=ci.regression,
                        regression_required=regression_required, failing=failing)
    ledger.add("test_result", f"check-runs@{head_sha[:10]}",
               f"tests={ci.tests} regression={ci.regression} failing={failing}", meta={"names": ci.names})

    # safety
    scanner = SecretScanner.from_environment()
    for f in git(path, "ls-files", check=False).splitlines():
        if guard.is_sensitive(f):
            scanner.learn_file(path / f)
    flags = [f.__dict__ for f in safety.scan(files, guard, scanner)]

    # fix check (skipped when hard failures already decide the outcome)
    criteria: list[CriterionResult] = []
    problems: list[Claim] = []
    hard_fail = bool(conflicts) or ci.tests == "failure" or bool(flags)
    if llm is not None and report is not None and not hard_fail and tests.ci == "complete":
        test_summary = f"tests: {ci.tests}; regression test: {ci.regression or 'none'}"
        try:
            out, u = review(llm, ledger, report, files, worktree, guard, pr.get("title") or "", pr.get("body") or "",
                            test_summary)
            usage.add(u)
            criteria, problems = out.criteria, out.problems
        except LLMError as e:
            log.warning("PR review failed: %s", e)

    v = PRVerdict(pr_number=pr_number, head_sha=head_sha, base_ref=base_ref, base_sha=base_sha,
                  linked_issue=issue_number, mergeable=mergeable,
                  conflicts=conflicts, changed_files=changed, out_of_scope_files=out_of_scope, affected=affected,
                  tests=tests, safety_flags=flags, criteria=criteria, issues_found=problems,
                  decision="escalate")
    decision, reasons = decide(v, policy)
    overridable = decision == "safety_hold" and not v.safety_flags and v.tests.ci == "complete" \
        and reasons == ["a maintainer needs to judge criteria the review couldn't confirm"]
    v = v.model_copy(update={"decision": decision, "reasons": reasons, "overridable": overridable})
    from mergemind import tracing

    tracing.attrs(repo=repo, issue=issue_number, pr=pr_number, head_sha=head_sha, decision=decision, reasons=reasons,
                  safety_flags=[f["kind"] for f in flags], ci_tests=ci.tests, ci_regression=ci.regression,
                  conflicts=len(conflicts), out_of_scope=out_of_scope, criteria=[c.met for c in criteria],
                  in_tokens=usage.in_tok, out_tokens=usage.out_tok)
    store.add_tokens(run_id, usage.in_tok, usage.out_tok)
    store.finish_run(run_id, "ok", verdict_json=v.model_dump_json())
    return Analysis(v, run_id, usage)
