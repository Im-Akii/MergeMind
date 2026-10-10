"""PR-side backtest without CI or LLM (buildplan-v2 §12.1, "PR verdict" rows):

- safety-scan hold rate on the dataset's merged fix PRs (all were accepted by maintainers,
  so every hold is a cost: a human has to look) and the flag breakdown;
- reverted fix PRs (found in default-branch history) and whether the scan flagged them;
- out-of-scope rate: fix-PR source files outside the scope MergeMind's localization would
  have allowed (taken from the localization backtest rows).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mergemind.code.graph import is_doc, is_test
from mergemind.code.repo_cache import git
from mergemind.code.scope import out_of_scope
from mergemind.evals.dataset import Item
from mergemind.github.client import GitHub, GitHubError
from mergemind.guard.paths import PathGuard
from mergemind.guard.secrets import SecretScanner
from mergemind.pr import diff, safety

_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
_REVERT_PR = re.compile(r"(?i)^revert\b.*\(#(\d+)\)|^revert \"[^\"]*\"")


@dataclass
class PRRow:
    repo: str
    issue: int
    pr: int
    flags: list[dict] = field(default_factory=list)
    reverted: bool = False
    changed_source: list[str] = field(default_factory=list)
    out_of_scope: list[str] | None = None
    error: str | None = None


def reverted_prs(repo_path: Path, branch: str) -> tuple[set[str], set[int]]:
    """(reverted commit shas, reverted PR numbers) from default-branch history."""
    out = git(repo_path, "log", f"origin/{branch}", "--format=%x1e%s%n%b", "-i", "--grep=revert", check=False)
    shas, prs = set(), set()
    for block in out.split("\x1e"):
        shas |= set(_REVERTS.findall(block))
        first = block.strip().splitlines()[0] if block.strip() else ""
        m = re.search(r"\(#(\d+)\)", first)
        if first.lower().startswith("revert") and m:
            # "Revert "Fix foo (#123)" (#130)": the reverted PR is the inner number
            nums = re.findall(r"#(\d+)", first)
            if nums:
                prs.add(int(nums[0]))
    return shas, prs


def run(gh: GitHub, items: list[Item], repo_path: Path, branch: str, scopes: dict[int, list[str]],
        sensitive: list[str]) -> list[PRRow]:
    shas, prs = reverted_prs(repo_path, branch)
    guard, scanner = PathGuard(sensitive), SecretScanner()
    own = safety.own_packages(git(repo_path, "ls-tree", "-r", "--name-only", f"origin/{branch}", check=False).splitlines())
    rows = []
    for it in items:
        row = PRRow(it.repo, it.number, it.pr_number)
        row.reverted = it.pr_number in prs or bool(it.merge_sha and any(it.merge_sha.startswith(s) for s in shas))
        try:
            files = diff.fetch_files(gh, it.repo, it.pr_number)
        except GitHubError as e:
            row.error = e.error_class
            rows.append(row)
            continue
        row.flags = [asdict(f) for f in safety.scan(files, guard, scanner, own=own)]
        row.changed_source = [f.path for f in files if not is_test(f.path) and not is_doc(f.path)]
        if it.number in scopes:
            new = {f.path for f in files if f.status == "added"}
            row.out_of_scope = out_of_scope([(p, p in new) for p in row.changed_source], scopes[it.number])
        rows.append(row)
    return rows


def summarize(rows: list[PRRow]) -> dict:
    ok = [r for r in rows if not r.error]
    held = [r for r in ok if r.flags]
    reverted = [r for r in ok if r.reverted]
    kinds = Counter(f["kind"] for r in ok for f in r.flags)
    kinds_per_pr = Counter(k for r in ok for k in {f["kind"] for f in r.flags})
    with_scope = [r for r in ok if r.out_of_scope is not None]
    return {
        "prs": len(ok),
        "held_by_safety_scan": len(held),
        "hold_rate": len(held) / len(ok) if ok else None,
        "flag_kinds_per_pr": dict(kinds_per_pr.most_common()),
        "flags_total": dict(kinds.most_common()),
        "reverted_prs": len(reverted),
        "reverted_and_flagged": sum(1 for r in reverted if r.flags),
        "not_reverted_flag_rate": (sum(1 for r in ok if not r.reverted and r.flags)
                                   / max(1, sum(1 for r in ok if not r.reverted))),
        "hold_rate_ci": _ci([float(bool(r.flags)) for r in ok]),
        "with_scope": len(with_scope),
        "any_out_of_scope_rate": (sum(1 for r in with_scope if r.out_of_scope) / len(with_scope)) if with_scope else None,
        "all_in_scope_rate": (sum(1 for r in with_scope if not r.out_of_scope) / len(with_scope)) if with_scope else None,
        "any_out_of_scope_rate_ci": _ci([float(bool(r.out_of_scope)) for r in with_scope]),
    }


def _ci(values: list[float]) -> list[float] | None:
    from mergemind.evals.metrics import bootstrap

    return list(bootstrap(values)) if values else None


def split_rows(rows: list[PRRow], items: list[Item]) -> tuple[list[PRRow], list[PRRow]]:
    """Same time split as the localization backtest: per repo, oldest 60% dev, newest 40% test."""
    order = sorted(items, key=lambda it: it.created_at)
    test_ids = {(it.repo, it.number) for it in order[int(len(order) * 0.6):]}
    dev = [r for r in rows if (r.repo, r.issue) not in test_ids]
    return dev, [r for r in rows if (r.repo, r.issue) in test_ids]


def save(rows: list[PRRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(asdict(r)) for r in rows) + "\n", encoding="utf-8")
