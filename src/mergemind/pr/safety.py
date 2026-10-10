"""PR safety scan (rules only). Any flag puts the PR on hold for a maintainer.
Flag details never include secret values.

v2 (precision, see evidence/eval/summary-v1.md): risky-call rules only look at source code
(not docs, tests, lockfiles or config), never flag the repo's own package name, and need a
real call boundary (`re.compile(` is not `compile(`); manifests that mix tool configuration
with dependencies (pyproject.toml, setup.cfg) are flagged only when a changed line can touch
dependencies; test files keep the exact secret patterns (provider tokens, private keys,
known values) but skip the guess-based ones that fire on fixture credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from mergemind.code.symbols import SOURCE_EXT
from mergemind.guard.paths import PathGuard
from mergemind.guard.secrets import SecretScanner
from mergemind.pr.diff import FileChange

CI_FILES = re.compile(r"^(\.github/(workflows|actions)/|\.gitlab-ci\.yml$|\.circleci/|azure-pipelines|Jenkinsfile|\.travis\.yml$)")
DEPENDENCY_FILES = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.cfg", "Pipfile", "Pipfile.lock",
    "poetry.lock", "uv.lock", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.mod",
    "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock", "composer.json", "composer.lock",
    "pom.xml", "build.gradle", "build.gradle.kts",
}
# files that hold tool configuration next to dependencies: only dependency-shaped changes count
MIXED_MANIFESTS = {"pyproject.toml", "setup.cfg"}
# conftest.py / pytest.ini are test code: they run in the same sandboxed job as the tests themselves
BUILD_FILES = {"setup.py", "Makefile", "noxfile.py", "tox.ini", "Dockerfile", "docker-compose.yml",
               "Rakefile", "build.rs", ".pre-commit-config.yaml", "MANIFEST.in"}
SCRIPT_EXT = {".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd"}
RISKY_CODE = re.compile(
    r"(?<![\w.])(eval|exec|compile)\s*\(|(?<![\w.])__import__\s*\(|\bos\.system\s*\(|\bsubprocess\.|"
    r"\bpickle\.loads?\s*\(|\bmarshal\.loads\s*\(|\bctypes\b|"
    r"\bsocket\.(socket|create_connection|create_server|getaddrinfo|gethostbyname|fromfd|socketpair)\b|"
    r"\burllib\.request\.(urlopen|Request|build_opener|urlretrieve)\b|"
    r"\bfrom\s+urllib\.request\s+import\s+.*\b(urlopen|Request|build_opener|urlretrieve)\b|\bhttp\.client|"
    r"\brequests\.(get|post|put)|\bhttpx\.|\bfetch\s*\(|\bXMLHttpRequest\b|child_process|\bcurl\s|\bwget\s|"
    r"base64\.b64decode|\bchmod\s|\bsetuid\b|/etc/passwd|\.ssh/")
LONG_LINE = 400
BLOB = re.compile(r"[A-Za-z0-9+/=]{200,}")

_DEP_KEY = re.compile(r"(?i)^\s*[\"']?(dependencies|optional-dependencies|dependency-groups|install_requires|"
                      r"setup_requires|tests_require|extras_require|requires|build-backend|"
                      r"\[project\.optional-dependencies|\[dependency-groups|\[build-system|\[options\.extras_require)")
_PKG_SPEC = re.compile(r"[\"']?[A-Za-z][\w.\-]*(\[[\w,\- ]+\])?\s*(==|>=|<=|~=|!=|===|<|>)\s*\d")
_SAFE_LINE = (
    re.compile(r"^\s*(#.*)?$"),                                   # blank / comment
    re.compile(r"^\s*\[tool\.[\w.\-\"]+\]\s*$"),                   # [tool.ruff], [tool.pytest.ini_options]
    re.compile(r"^\s*[\w.\-]+\s*[:=]"),                            # key = value (dependency keys excluded below)
    re.compile(r"^\s*[\"'][^\"']*[/:* ][^\"']*[\"'],?\s*$"),      # list item that can't be a package name
    re.compile(r"^\s*[\]\)\}],?\s*$"),                            # closing bracket
)


@dataclass(frozen=True)
class SafetyFlag:
    kind: str
    file: str
    detail: str
    line: int | None = None


def own_packages(files: list[str]) -> set[str]:
    """Top-level import packages of the repo itself (`httpx`, `_pytest`, …) from its file list."""
    out = set()
    for f in files:
        parts = f.split("/")
        if parts[0] == "src" and len(parts) >= 3 and parts[2] == "__init__.py":
            out.add(parts[1])
        elif len(parts) == 2 and parts[1] == "__init__.py":
            out.add(parts[0])
    return out


def _dependency_change(fc: FileChange) -> bool:
    name = PurePosixPath(fc.path).name
    if name not in MIXED_MANIFESTS:
        return True
    lines = [t for _, t in fc.added] + list(fc.removed)
    if not lines and (fc.additions or fc.deletions or fc.status in ("added", "removed")):
        return True  # no patch to look at: be conservative
    for t in lines:
        if _DEP_KEY.search(t) or _PKG_SPEC.search(t):
            return True
        if not any(p.search(t) for p in _SAFE_LINE):
            return True  # e.g. a bare `'twine',` list item could be a new dependency
    return False


def _code_file(path: str) -> bool:
    from mergemind.code.graph import is_doc

    suffix = PurePosixPath(path).suffix
    return not _is_test(path) and not is_doc(path) and (suffix in SOURCE_EXT or suffix in SCRIPT_EXT)


def _risky(text: str, own: set[str]) -> str | None:
    for m in RISKY_CODE.finditer(text):
        word = m.group(0).strip()
        if word.endswith(".") and word[:-1] in own:
            continue  # `httpx.` inside httpx is the library calling itself
        return word
    return None


def scan(files: list[FileChange], guard: PathGuard, scanner: SecretScanner,
         repo_has_dependency_file: set[str] | None = None, own: set[str] | None = None) -> list[SafetyFlag]:
    from mergemind.code.graph import is_doc

    own = own or set()
    flags: list[SafetyFlag] = []
    for fc in files:
        name = PurePosixPath(fc.path).name
        test = _is_test(fc.path)
        if CI_FILES.search(fc.path):
            flags.append(SafetyFlag("ci_config", fc.path, "changes CI/workflow configuration"))
        if name in DEPENDENCY_FILES and _dependency_change(fc):
            flags.append(SafetyFlag("dependencies", fc.path, "changes dependencies or lockfiles"))
        if name in BUILD_FILES:
            flags.append(SafetyFlag("build_script", fc.path, "changes a build/test bootstrap file that runs in CI"))
        if guard.is_sensitive(fc.path) or (fc.previous_path and guard.is_sensitive(fc.previous_path)):
            flags.append(SafetyFlag("restricted_file", fc.path, "touches a restricted file"))
        if fc.binary and not (fc.status in ("renamed", "copied") and fc.previous_path):
            flags.append(SafetyFlag("binary", fc.path, "adds or changes a binary file"))
        if fc.status == "removed" and not test:
            flags.append(SafetyFlag("deletion", fc.path, "deletes a non-test file"))
        code = _code_file(fc.path)
        for line_no, text in fc.added:
            if BLOB.search(text) or (len(text) > LONG_LINE and not is_doc(fc.path)):
                flags.append(SafetyFlag("obfuscation", fc.path, "very long line or encoded blob", line_no))
            risky = _risky(text, own) if code else None
            if risky:
                flags.append(SafetyFlag("risky_call", fc.path, f"adds `{risky}`", line_no))
            if scanner.scan(text, heuristics=not test, code=True):
                flags.append(SafetyFlag("secret", fc.path, "adds something that looks like a credential", line_no))
    return _dedupe(flags)


def _is_test(path: str) -> bool:
    from mergemind.code.graph import is_test

    return is_test(path)


def _dedupe(flags: list[SafetyFlag]) -> list[SafetyFlag]:
    seen: set[tuple[str, str, str]] = set()
    out = []
    for f in flags:
        k = (f.kind, f.file, f.detail)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out
