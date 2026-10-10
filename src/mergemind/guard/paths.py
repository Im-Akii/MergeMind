"""Sensitive-path policy: files that are never shown to the LLM or the contributor."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

ALWAYS_SENSITIVE = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "*.keystore",
                    ".npmrc", ".pypirc", ".netrc", "credentials*", "secrets.*"]


class PathGuard:
    def __init__(self, patterns: list[str] | None = None):
        self.patterns = list(patterns or [])

    def is_sensitive(self, path: str) -> bool:
        p = path.replace("\\", "/").lstrip("./")
        name = PurePosixPath(p).name
        if any(fnmatchcase(name, pat) for pat in ALWAYS_SENSITIVE):
            return True
        for pat in self.patterns:
            if fnmatchcase(p, pat) or fnmatchcase(name, pat):
                return True
            # "deploy/**" also covers "deploy/x" and deeper
            if pat.endswith("/**") and (p == pat[:-3] or p.startswith(pat[:-2])):
                return True
            if pat.startswith("**/") and fnmatchcase(p, pat[3:]):
                return True
        return False

    def filter(self, paths: list[str]) -> list[str]:
        return [p for p in paths if not self.is_sensitive(p)]
