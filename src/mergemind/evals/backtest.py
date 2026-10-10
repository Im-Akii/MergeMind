"""Leakage-free localization backtest (buildplan-v2 §12.1). No LLM.

For each historical issue the code is checked out at the last default-branch commit before
the start of the issue's month, so the fix (and its blame) can't leak into the answer.
Methods compared on the fix PR's modified source files:

  entire    MergeMind localization with entire-graph        (the product)
  lexical   MergeMind localization with the git-grep backend (ablation: what entire adds)
  mentioned files named in the issue text                    (baseline)
  recency   files changed most in the 180 days before        (baseline, ignores the issue)
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mergemind import tracing
from mergemind.code.graph import EntireIntel, LexicalIntel, entire_binary, is_doc, is_test
from mergemind.code.localize import localize
from mergemind.code.repo_cache import RepoCache, git
from mergemind.code.signals import extract
from mergemind.code.symbols import SOURCE_EXT
from mergemind.evals.dataset import Item
from mergemind.text import clean

log = logging.getLogger(__name__)

METHODS = ("entire", "lexical", "mentioned", "recency")


@dataclass
class Row:
    repo: str
    number: int
    created_at: str
    snapshot: str
    truth: list[str]  # fix files that existed at the snapshot
    truth_tests: list[str]
    ranked: dict[str, list[str]] = field(default_factory=dict)  # method -> ranked files
    scope: dict[str, list[str]] = field(default_factory=dict)
    tests: dict[str, list[str]] = field(default_factory=dict)
    confidence: dict[str, str] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    error: str | None = None


def snapshot_sha(repo_path: Path, branch: str, created_at: str) -> str | None:
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    month_start = created.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = git(repo_path, "rev-list", "-1", "--first-parent", f"--before={month_start.isoformat()}",
              f"origin/{branch}", check=False).strip()
    return out or None


def recency_files(worktree: Path, k: int = 10, days: int = 180) -> list[str]:
    head_date = git(worktree, "log", "-1", "--format=%cI", check=False).strip()
    since = (datetime.fromisoformat(head_date) - timedelta(days=days)).isoformat() if head_date else None
    args = ["log", "--format=", "--name-only", "--no-merges"] + ([f"--since={since}"] if since else ["-n500"])
    counts = Counter(f for f in git(worktree, *args, check=False).splitlines()
                     if f and Path(f).suffix in SOURCE_EXT and not is_test(f) and not is_doc(f))
    present = {f for f in git(worktree, "ls-files", check=False).splitlines()}
    return [f for f, _ in counts.most_common(k * 3) if f in present][:k]


def _unique_files(regions) -> list[str]:
    return list(dict.fromkeys(r.file for r in regions))


@tracing.span("WORKFLOW", "backtest_issue")
def run_item(item: Item, cache: RepoCache, branch: str, methods: tuple[str, ...] = METHODS,
             entire_bin: str | None = None) -> Row:
    repo_path = cache.path(item.repo)
    sha = snapshot_sha(repo_path, branch, item.created_at)
    row = Row(item.repo, item.number, item.created_at, sha or "", [], [])
    if sha is None:
        row.error = "no snapshot"
        return row
    wt = cache.worktree(item.repo, sha)
    files = [f for f in git(wt, "ls-files", check=False).splitlines() if f]
    present = set(files)
    row.truth = [f for f in item.fix_files if f in present]
    row.truth_tests = [f for f in item.test_files if f in present]
    if not row.truth:
        row.error = "fix files did not exist at the snapshot"
        return row
    title, body = clean(item.title, 300), clean(item.body)
    sig = extract(f"{title}\n{body}", present)

    for m in methods:
        t = time.monotonic()
        if m in ("entire", "lexical"):
            lexical = LexicalIntel(wt)
            if m == "entire":
                if not entire_bin:
                    continue
                intel = EntireIntel(wt, lexical, entire_bin)
            else:
                intel = lexical
            loc = localize(intel, wt, title, body, sig, top_k=10, sha=sha)
            row.ranked[m] = _unique_files(loc.regions)
            row.scope[m] = loc.scope_files
            row.tests[m] = sorted({t for imp in loc.impacts for t in imp.tests})
            row.confidence[m] = loc.confidence
        elif m == "mentioned":
            row.ranked[m] = [p for p in sig.paths if not is_test(p) and not is_doc(p)]
        elif m == "recency":
            row.ranked[m] = recency_files(wt)
        row.seconds[m] = round(time.monotonic() - t, 2)
    tracing.attrs(repo=item.repo, issue=item.number, snapshot=sha, truth=row.truth,
                  **{f"hit5_{m}": bool(set(row.ranked.get(m, [])[:5]) & set(row.truth)) for m in row.ranked})
    return row


def rows_path(work_dir: Path, repo: str, run_id: str) -> Path:
    return Path(work_dir) / "eval" / repo.replace("/", "__") / f"locate-{run_id}.jsonl"


def run(items: list[Item], cache: RepoCache, branch: str, out: Path, methods: tuple[str, ...] = METHODS,
        warm_entire: bool = True, workers: int = 4) -> list[Row]:
    """Resumable: rows already in `out` are skipped. Items are grouped by snapshot so each
    worktree (and its graph index) is built once; snapshots run in parallel."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    done = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = Row(**json.loads(line))
            done[r.number] = r
    entire_bin = entire_binary() if "entire" in methods else None
    repo_path = cache.path(items[0].repo) if items else None
    by_snapshot: dict[str, list[Item]] = {}
    for it in items:
        if it.number in done:
            continue
        sha = snapshot_sha(repo_path, branch, it.created_at) or ""
        by_snapshot.setdefault(sha, []).append(it)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = list(done.values())
    total = sum(len(v) for v in by_snapshot.values())
    counter = [0]
    git_lock, out_lock = threading.Lock(), threading.Lock()  # git worktree add takes repo-wide locks

    def process(sha: str, group: list[Item]) -> None:
        if sha:
            with git_lock:
                wt = cache.worktree(group[0].repo, sha)
            if entire_bin and warm_entire:
                EntireIntel(wt, LexicalIntel(wt), entire_bin).index()
        for it in group:
            try:
                row = run_item(it, cache, branch, methods, entire_bin)
            except Exception as e:  # one bad item must not stop the run
                log.exception("item %s#%d failed", it.repo, it.number)
                row = Row(it.repo, it.number, it.created_at, sha, [], [], error=f"{type(e).__name__}: {e}")
            with out_lock:
                rows.append(row)
                counter[0] += 1
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(row)) + "\n")
                log.info("[%d/%d] %s#%d snapshot=%s hit5: %s", counter[0], total, it.repo, it.number, sha[:8],
                         {m: bool(set(row.ranked.get(m, [])[:5]) & set(row.truth)) for m in row.ranked})

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for fut in [pool.submit(process, sha, group) for sha, group in by_snapshot.items()]:
            fut.result()
    return rows


def load_rows(path: Path) -> list[Row]:
    return [Row(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
