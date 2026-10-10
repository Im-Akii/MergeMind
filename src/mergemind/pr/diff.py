"""PR diff: changed files and hunks (from the API), PR refs and merge conflicts (local git)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mergemind.code import symbols as sym
from mergemind.code.repo_cache import git
from mergemind.github.client import GitHub

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_LINKED = re.compile(r"(?i)\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*:?\s+#(\d+)\b")
_BRANCH = re.compile(r"(?:^|/)issue-(\d+)\b")


@dataclass
class FileChange:
    path: str
    status: str  # added | modified | removed | renamed | copied | changed
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_path: str | None = None
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line number, text)
    removed: list[str] = field(default_factory=list)

    @property
    def binary(self) -> bool:
        return self.patch is None and self.status != "removed" and (self.additions + self.deletions) == 0

    @property
    def changed_lines(self) -> list[int]:
        return [n for n, _ in self.added]


def parse_patch(patch: str) -> tuple[list[tuple[int, str]], list[str]]:
    added: list[tuple[int, str]] = []
    removed: list[str] = []
    new_line = 0
    for line in (patch or "").splitlines():
        m = _HUNK.match(line)
        if m:
            new_line = int(m.group(3))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
        elif line.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            new_line += 1
    return added, removed


def fetch_files(gh: GitHub, repo: str, pr: int) -> list[FileChange]:
    out = []
    for f in gh.paginate(f"/repos/{repo}/pulls/{pr}/files", limit=3000):
        fc = FileChange(path=f["filename"], status=f["status"], additions=f.get("additions", 0),
                        deletions=f.get("deletions", 0), patch=f.get("patch"),
                        previous_path=f.get("previous_filename"))
        fc.added, fc.removed = parse_patch(fc.patch or "")
        out.append(fc)
    return out


def linked_issue(pr: dict) -> int | None:
    """`Fixes #N` in the body/title, else a `…issue-N` branch name."""
    m = _LINKED.search(f"{pr.get('title') or ''}\n{pr.get('body') or ''}")
    if m:
        return int(m.group(1))
    m = _BRANCH.search((pr.get("head") or {}).get("ref") or "")
    return int(m.group(1)) if m else None


def merge_conflicts(repo_path: Path, base: str, head: str) -> list[str]:
    """Files that conflict when merging head into base (git merge-tree, no worktree changes)."""
    out = git(repo_path, "merge-tree", "--write-tree", "--name-only", "--no-messages", base, head, check=False)
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[1:]  # first line is the tree id; the rest are conflicted paths


def changed_symbols(worktree: Path, files: list[FileChange], limit: int = 15) -> list[tuple[str, sym.Symbol]]:
    """(file, symbol) for every function/class whose lines the PR changed (new side)."""
    seen: dict[tuple[str, str], sym.Symbol] = {}
    for fc in files:
        if fc.status == "removed":
            continue
        for line in fc.changed_lines:
            s = sym.enclosing(worktree / fc.path, line)
            if s and (fc.path, s.name) not in seen:
                seen[(fc.path, s.name)] = s
    return [(f, s) for (f, _), s in list(seen.items())[:limit]]
