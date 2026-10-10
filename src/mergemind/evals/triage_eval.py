"""LLM-stage backtest (buildplan-v2 §12.1 "LLM stages run on a sample"): the full triage
pipeline (investigator + verifier + duplicates) on historical issues, time-travelled.

The investigator sees only the issue as opened (title + body, no comments: later comments
often contain the answer) and the code at the issue-month snapshot. Every run is tagged
with its eval run id in the `runs` table and in neatlogs.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mergemind.code.repo_cache import RepoCache
from mergemind.config import Policy, Settings
from mergemind.evals.backtest import snapshot_sha
from mergemind.evals.dataset import Item
from mergemind.github.client import GitHub
from mergemind.llm import LLM
from mergemind.store.db import Store
from mergemind.triage import triage_issue

log = logging.getLogger(__name__)


class EvalGitHub:
    """Real GitHub for history (issue index), but the issue itself as it was when opened."""

    def __init__(self, gh: GitHub, item: Item):
        self.gh, self.item = gh, item
        self.store = gh.store

    def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        it = self.item
        return {"number": it.number, "title": it.title, "body": it.body, "user": {"login": it.author},
                "created_at": it.created_at, "updated_at": it.created_at, "state": "open", "comments": 0,
                "labels": []}

    def list_issue_comments(self, repo: str, number: int) -> list:
        return []

    def get_repo(self, repo: str) -> dict[str, Any]:
        return {"private": False}

    def __getattr__(self, name: str) -> Any:  # paginate, request, … go to the real client
        return getattr(self.gh, name)


class SnapshotCache:
    """RepoCache view that serves the issue-month snapshot worktree instead of HEAD."""

    def __init__(self, cache: RepoCache, sha: str):
        self.cache, self.sha = cache, sha

    def ensure(self, repo: str, fetch: bool = True, private: bool = False) -> Path:
        return self.cache.worktree(repo, self.sha)

    def head(self, repo: str, branch: str | None = None) -> str:
        return self.sha

    def checkout_detached(self, repo: str, sha: str) -> Path:
        return self.cache.worktree(repo, sha)


@dataclass
class TriageRow:
    repo: str
    number: int
    created_at: str
    snapshot: str
    truth: list[str]
    disposition: str = ""
    issue_type: str = ""
    localized: list[str] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)
    verifier_ok: bool | None = None
    tool_calls: int = 0
    repairs: int = 0
    in_tok: int = 0
    out_tok: int = 0
    cached: int = 0
    seconds: float = 0.0
    run_id: int | None = None
    by_model: dict = field(default_factory=dict)
    error: str | None = None


def run_one(settings: Settings, store: Store, gh: GitHub, llm: LLM, cache: RepoCache, item: Item,
            branch: str, run_id: str) -> TriageRow:
    sha = snapshot_sha(cache.path(item.repo), branch, item.created_at)
    row = TriageRow(item.repo, item.number, item.created_at, sha or "", [])
    if not sha:
        row.error = "no snapshot"
        return row
    wt = cache.worktree(item.repo, sha)
    row.truth = [f for f in item.fix_files if (wt / f).exists()]
    if not row.truth:
        row.error = "fix files did not exist at the snapshot"
        return row
    t = time.monotonic()
    try:
        out = triage_issue(settings=settings, store=store, gh=EvalGitHub(gh, item), llm=llm, policy=Policy(),
                           repo=item.repo, number=item.number, cache=SnapshotCache(cache, sha), as_of_sha=sha,
                           eval_run_id=run_id, fetch=False)
    except Exception as e:
        log.exception("triage %s#%d failed", item.repo, item.number)
        row.error = f"{type(e).__name__}: {str(e)[:200]}"
        return row
    r = out.report
    row.disposition, row.issue_type = r.disposition, r.issue_type
    row.localized = list(dict.fromkeys(x.file for x in r.localization))
    row.scope = sorted({x.file for x in r.scope})
    row.verifier_ok = None if out.verdict is None else out.verdict.ok
    row.tool_calls, row.repairs = out.tool_calls, out.repairs
    row.in_tok, row.out_tok, row.cached = out.usage.in_tok, out.usage.out_tok, out.usage.cached
    row.seconds, row.run_id = round(time.monotonic() - t, 1), out.run_id
    row.by_model = out.usage.by_model
    return row


def run(settings: Settings, store: Store, gh: GitHub, llm: LLM, cache: RepoCache, items: list[Item], branch: str,
        run_id: str, out: Path) -> list[TriageRow]:
    done = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = TriageRow(**json.loads(line))
            done[r.number] = r
    rows = list(done.values())
    todo = [it for it in items if it.number not in done]
    for i, it in enumerate(todo, 1):
        row = run_one(settings, store, gh, llm, cache, it, branch, run_id)
        rows.append(row)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(row)) + "\n")
        log.info("[%d/%d] %s#%d -> %s localized=%s truth=%s tokens=%d/%d %.0fs %s", i, len(todo), it.repo, it.number,
                 row.disposition, row.localized[:3], row.truth[:3], row.in_tok, row.out_tok, row.seconds,
                 row.error or "")
    return rows


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))] if s else float("nan")


def summarize(rows: list[TriageRow]) -> dict:
    ok = [r for r in rows if not r.error]
    fresh = [r for r in ok if r.cached == 0 or r.in_tok > 0]
    hit = lambda r, k: bool(set(r.localized[:k]) & set(r.truth))  # noqa: E731
    disp = {}
    for r in ok:
        disp[r.disposition] = disp.get(r.disposition, 0) + 1
    accepted = [r for r in ok if r.disposition == "accept"]
    toks = [r.in_tok + r.out_tok for r in fresh]
    return {
        "issues": len(ok),
        "errors": len(rows) - len(ok),
        "dispositions": disp,
        "accept_rate": len(accepted) / len(ok) if ok else None,
        "localization_hit@1": sum(hit(r, 1) for r in ok) / len(ok) if ok else None,
        "localization_hit@3": sum(hit(r, 3) for r in ok) / len(ok) if ok else None,
        "accepted_scope_coverage": (statistics.fmean(len(set(r.scope) & set(r.truth)) / len(r.truth) for r in accepted)
                                    if accepted else None),
        "verifier_rejected_rate": sum(1 for r in ok if r.verifier_ok is False) / len(ok) if ok else None,
        "repair_rate": sum(1 for r in ok if r.repairs) / len(ok) if ok else None,
        "tool_calls_median": statistics.median(r.tool_calls for r in ok) if ok else None,
        "in_tokens_median": statistics.median(r.in_tok for r in fresh) if fresh else None,
        "out_tokens_median": statistics.median(r.out_tok for r in fresh) if fresh else None,
        "tokens_median": statistics.median(toks) if toks else None,
        "tokens_p90": _pct(toks, 0.9) if toks else None,
        "seconds_median": statistics.median(r.seconds for r in fresh) if fresh else None,
        "seconds_p90": _pct([r.seconds for r in fresh], 0.9) if fresh else None,
    }
