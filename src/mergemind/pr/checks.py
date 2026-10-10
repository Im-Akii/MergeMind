"""Read the untrusted-test results from the `mergemind-verify` workflow (check runs).

The engine never runs PR code. The workflow (pull_request trigger, read-only token, no
secrets) reports two jobs, `tests` and `regression`, as check runs on the PR head commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mergemind.github.client import GitHub, GitHubError

TESTS_JOB = "tests"
REGRESSION_JOB = "regression"
_FAILED = re.compile(r"(?:FAILED|ERROR)\s+([\w/.\-]+\.py::[\w\[\]\-.:,= ]+?)(?:\s+-\s|\s*$)", re.M)


@dataclass
class CIState:
    status: str  # pending | complete | missing
    tests: str | None = None  # success | failure | cancelled | timed_out | skipped | None
    regression: str | None = None
    tests_job_id: int | None = None
    regression_job_id: int | None = None
    names: list[str] = field(default_factory=list)


def check_runs(gh: GitHub, repo: str, sha: str) -> list[dict]:
    data = gh.request("GET", f"/repos/{repo}/commits/{sha}/check-runs", point="gh.check_runs",
                      params={"per_page": 100})
    return (data or {}).get("check_runs", [])


def summarize(runs: list[dict]) -> CIState:
    by_name: dict[str, dict] = {}
    for r in runs:
        # newest run per name wins (re-runs)
        prev = by_name.get(r["name"])
        if prev is None or (r.get("started_at") or "") >= (prev.get("started_at") or ""):
            by_name[r["name"]] = r
    st = CIState(status="missing", names=sorted(by_name))
    t, g = by_name.get(TESTS_JOB), by_name.get(REGRESSION_JOB)
    if t is None:
        return st
    if t["status"] != "completed" or (g is not None and g["status"] != "completed"):
        st.status = "pending"
        return st
    st.status = "complete"
    st.tests, st.tests_job_id = t.get("conclusion"), t.get("id")
    if g is not None:
        st.regression, st.regression_job_id = g.get("conclusion"), g.get("id")
    return st


def failing_tests(gh: GitHub, repo: str, job_id: int | None, limit: int = 15) -> list[str]:
    """Test ids from a failed job's log. Only names are kept: logs can carry environment details."""
    if not job_id:
        return []
    try:
        log = gh.raw(f"/repos/{repo}/actions/jobs/{job_id}/logs", point="gh.job_logs")
    except GitHubError:
        return []
    # strip the timestamp prefix GitHub adds to each line
    log = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ", "", log, flags=re.M)
    names = list(dict.fromkeys(m.group(1).strip() for m in _FAILED.finditer(log)))
    return names[:limit]
