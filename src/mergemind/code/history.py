"""Line-level git history for a code region (feeds the briefing's History section)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from mergemind.code.repo_cache import git
from mergemind.tracing import span

_NOISE = re.compile(r"\b(format|formatting|black|ruff|lint|isort|prettier|bump|release|version|rename|vendor|typo)\b", re.I)
_SEP = "\x1e"


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    date: str  # ISO
    subject: str
    noise: bool = False


@span("TOOL", "git_history")
def region_commits(repo: Path, file: str, start: int, end: int, before: str | None = None, n: int = 20) -> list[Commit]:
    """Commits that touched exactly lines start..end of `file` (git log -L)."""
    args = ["log", f"-L{start},{end}:{file}", f"-n{n}", "-s",
            f"--format={_SEP}%H%x1f%an%x1f%ae%x1f%aI%x1f%s"]
    if before:
        args.insert(1, f"--before={before}")
    out = git(repo, *args, check=False, timeout=120)
    commits: list[Commit] = []
    for chunk in out.split(_SEP):
        head = chunk.strip().splitlines()[0] if chunk.strip() else ""
        parts = head.split("\x1f")
        if len(parts) == 5:
            c = Commit(*parts)
            c.noise = bool(_NOISE.search(c.subject))
            commits.append(c)
    return commits


def pr_number(subject: str) -> int | None:
    """'Fix foo (#1234)' / 'Merge pull request #1234 from …' → 1234."""
    m = re.search(r"\(#(\d+)\)\s*$|pull request #(\d+)", subject)
    return int(m.group(1) or m.group(2)) if m else None
