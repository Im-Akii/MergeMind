"""Backtest dataset (buildplan-v2 §12.1): closed issues fixed by exactly one merged PR.

Ground truth comes from the fix PR: the non-test, non-doc source files it modified
(what localization should find), the test files it changed (what impact should find),
and its merge commit (to detect later reverts).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mergemind.code.graph import is_doc, is_test
from mergemind.code.symbols import SOURCE_EXT
from mergemind.github.client import GitHub, GitHubError

log = logging.getLogger(__name__)

QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    issues(first: 40, after: $cursor, states: CLOSED, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body createdAt closedAt stateReason
        author { login __typename }
        labels(first: 10) { nodes { name } }
        closedByPullRequestsReferences(first: 5, includeClosedPrs: false) {
          nodes {
            number title merged mergedAt baseRefName
            author { login }
            mergeCommit { oid }
            files(first: 100) { nodes { path changeType } }
          }
        }
      }
    }
  }
}
"""


@dataclass
class Item:
    repo: str
    number: int
    title: str
    body: str
    created_at: str
    closed_at: str
    labels: list[str]
    author: str
    pr_number: int
    pr_title: str
    pr_merged_at: str
    pr_author: str
    merge_sha: str | None
    fix_files: list[str]  # modified source files: the localization target
    test_files: list[str]  # test files the fix touched
    all_files: list[str] = field(default_factory=list)


def _source(path: str) -> bool:
    return Path(path).suffix in SOURCE_EXT and not is_test(path) and not is_doc(path)


def build(gh: GitHub, repo: str, limit: int = 300, max_pages: int = 40) -> tuple[list[Item], dict]:
    owner, name = repo.split("/")
    items: list[Item] = []
    stats = {"issues_seen": 0, "no_merged_pr": 0, "multi_pr": 0, "bot_author": 0, "no_source_fix": 0,
             "wrong_base": 0, "empty_body": 0}
    cursor = None
    default_branch = None
    for _ in range(max_pages):
        data = gh.graphql(QUERY, {"owner": owner, "name": name, "cursor": cursor})["repository"]
        default_branch = default_branch or (data.get("defaultBranchRef") or {}).get("name", "main")
        page = data["issues"]
        for n in page["nodes"]:
            stats["issues_seen"] += 1
            prs = [p for p in n["closedByPullRequestsReferences"]["nodes"] if p.get("merged")]
            if not prs:
                stats["no_merged_pr"] += 1
                continue
            if len(prs) > 1:
                stats["multi_pr"] += 1
                continue
            pr = prs[0]
            author = n.get("author") or {}
            if author.get("__typename") == "Bot" or (author.get("login") or "").endswith("[bot]"):
                stats["bot_author"] += 1
                continue
            if pr.get("baseRefName") != default_branch:
                stats["wrong_base"] += 1
                continue
            if len((n.get("body") or "").strip()) < 20:
                stats["empty_body"] += 1
                continue
            files = pr["files"]["nodes"]
            # only files that existed before the fix can be localized
            fix = sorted({f["path"] for f in files if f["changeType"] in ("MODIFIED", "CHANGED", "DELETED")
                          and _source(f["path"])})
            if not fix:
                stats["no_source_fix"] += 1
                continue
            items.append(Item(
                repo=repo, number=n["number"], title=n["title"], body=n.get("body") or "",
                created_at=n["createdAt"], closed_at=n["closedAt"],
                labels=[lb["name"] for lb in n["labels"]["nodes"]], author=author.get("login") or "",
                pr_number=pr["number"], pr_title=pr["title"], pr_merged_at=pr["mergedAt"],
                pr_author=(pr.get("author") or {}).get("login") or "",
                merge_sha=(pr.get("mergeCommit") or {}).get("oid"),
                fix_files=fix, test_files=sorted({f["path"] for f in files if is_test(f["path"])}),
                all_files=sorted({f["path"] for f in files}),
            ))
            if len(items) >= limit:
                break
        if len(items) >= limit or not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    stats["kept"] = len(items)
    stats["default_branch"] = default_branch
    return items, stats


def save(items: list[Item], stats: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(asdict(it)) + "\n")
    path.with_suffix(".stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")


def load(path: Path) -> list[Item]:
    return [Item(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dataset_path(work_dir: Path, repo: str) -> Path:
    return Path(work_dir) / "eval" / repo.replace("/", "__") / "dataset.jsonl"


def fetch(gh: GitHub, repo: str, work_dir: Path, limit: int = 300) -> tuple[list[Item], dict]:
    try:
        items, stats = build(gh, repo, limit)
    except GitHubError as e:
        raise RuntimeError(f"dataset fetch for {repo} failed: {e}") from e
    save(items, stats, dataset_path(work_dir, repo))
    return items, stats
