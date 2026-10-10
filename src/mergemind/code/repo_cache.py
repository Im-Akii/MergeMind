"""Local clones of target repos, plus worktrees pinned at historical commits."""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class GitError(Exception):
    pass


_SECRETISH = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:Bearer|Basic)\s+\S+)")


def redact(text: str) -> str:
    return _SECRETISH.sub("[REDACTED]", text)


def git(
    cwd: Path | str,
    *args: str,
    timeout: float = 120,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.longpaths=true", "-c", "core.quotepath=off", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, **env} if env else None,
    )
    if check and proc.returncode != 0:
        # name the subcommand only: arguments may carry credentials
        sub, it = "?", iter(args)
        for a in it:
            if a == "-c":
                next(it, None)  # skip the config value; it may hold credentials
            elif not a.startswith("-"):
                sub = a
                break
        raise GitError(redact(f"git {sub} failed: {proc.stderr.strip()[:500]}"))
    return proc.stdout


def _auth_env(token: str | None) -> dict[str, str] | None:
    """Pass the token through git's env-based config, never argv or .git/config."""
    if not token:
        return None
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    }


class RepoCache:
    def __init__(self, work_dir: Path, token: str | None = None):
        self.root = (Path(work_dir) / "repos").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token

    def path(self, repo: str) -> Path:
        return self.root / repo.replace("/", "__")

    def ensure(self, repo: str, fetch: bool = True, private: bool = False) -> Path:
        """Clone on first use; fetch afterwards. Public repos are cloned without credentials."""
        dest = self.path(repo)
        auth = _auth_env(self.token) if private else None
        if not (dest / ".git").exists():
            log.info("cloning %s", repo)
            url = f"https://github.com/{repo}.git"
            git(self.root, "clone", "--no-tags", url, str(dest), timeout=900, env=auth)
        elif fetch:
            git(dest, "fetch", "--prune", "origin", timeout=600, env=auth)
        return dest

    def head(self, repo: str, branch: str | None = None) -> str:
        dest = self.path(repo)
        ref = f"origin/{branch}" if branch else "origin/HEAD"
        return git(dest, "rev-parse", ref).strip()

    def checkout_detached(self, repo: str, sha: str) -> Path:
        """Main checkout moved to `sha` (live runs analyse origin HEAD)."""
        dest = self.path(repo)
        git(dest, "checkout", "--detach", "--force", sha)
        return dest

    def fetch_pr(self, repo: str, pr: int, private: bool = False) -> None:
        """Fetch a PR's head commit (works for forks) into refs/mergemind/pr/<n>/head."""
        git(self.path(repo), "fetch", "--no-tags", "origin", f"+refs/pull/{pr}/head:refs/mergemind/pr/{pr}/head",
            timeout=600, env=_auth_env(self.token) if private else None)

    def revert_and_push(self, repo: str, sha: str, base: str, branch: str) -> None:
        """Create `branch` from origin/<base> with `sha` reverted, and push it. Never touches <base>."""
        dest = self.path(repo)
        auth = _auth_env(self.token)
        git(dest, "fetch", "--no-tags", "origin", base, timeout=600, env=auth)
        wt = self.root / "_worktrees" / f"revert-{repo.replace('/', '__')}-{sha[:8]}"
        if wt.exists():
            git(dest, "worktree", "remove", "--force", str(wt), check=False)
        git(dest, "worktree", "add", "--force", "-B", branch, str(wt), f"origin/{base}", timeout=600)
        try:
            parents = git(wt, "rev-list", "--parents", "-n", "1", sha).split()
            mainline = ["-m", "1"] if len(parents) > 2 else []
            git(wt, "-c", "user.name=MergeMind", "-c", "user.email=mergemind@users.noreply.github.com",
                "revert", "--no-edit", *mainline, sha)
            git(wt, "push", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}", timeout=600, env=auth)
        finally:
            git(dest, "worktree", "remove", "--force", str(wt), check=False)

    def worktree(self, repo: str, sha: str) -> Path:
        """A separate worktree at `sha` for historical (backtest) analysis."""
        dest = self.path(repo)
        wt = self.root / "_worktrees" / f"{repo.replace('/', '__')}@{sha[:12]}"
        if not wt.exists():
            wt.parent.mkdir(parents=True, exist_ok=True)
            git(dest, "worktree", "add", "--detach", "--force", str(wt), sha, timeout=600)
        return wt

    def ls_files(self, path: Path) -> list[str]:
        return [p for p in git(path, "ls-files").splitlines() if p]
