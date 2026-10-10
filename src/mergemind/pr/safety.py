"""PR safety scan (rules only). Any flag puts the PR on hold for a maintainer.
Flag details never include secret values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

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
BUILD_FILES = {"setup.py", "Makefile", "conftest.py", "noxfile.py", "tox.ini", "Dockerfile", "docker-compose.yml",
               "Rakefile", "build.rs", "pytest.ini", ".pre-commit-config.yaml", "MANIFEST.in"}
RISKY_CODE = re.compile(
    r"\b(eval|exec|compile)\s*\(|\b__import__\s*\(|\bos\.system\s*\(|\bsubprocess\.|\bpickle\.loads?\s*\(|"
    r"\bmarshal\.loads\s*\(|\bctypes\b|\bsocket\.|\burllib\.request|\bhttp\.client|\brequests\.(get|post|put)|"
    r"\bhttpx\.|\bfetch\s*\(|\bXMLHttpRequest\b|child_process|\bcurl\s|\bwget\s|base64\.b64decode|"
    r"\bchmod\s|\bsetuid\b|/etc/passwd|\.ssh/")
LONG_LINE = 400
BLOB = re.compile(r"[A-Za-z0-9+/=]{200,}")


@dataclass(frozen=True)
class SafetyFlag:
    kind: str
    file: str
    detail: str
    line: int | None = None


def scan(files: list[FileChange], guard: PathGuard, scanner: SecretScanner,
         repo_has_dependency_file: set[str] | None = None) -> list[SafetyFlag]:
    flags: list[SafetyFlag] = []
    for fc in files:
        name = PurePosixPath(fc.path).name
        if CI_FILES.search(fc.path):
            flags.append(SafetyFlag("ci_config", fc.path, "changes CI/workflow configuration"))
        if name in DEPENDENCY_FILES:
            flags.append(SafetyFlag("dependencies", fc.path, "changes dependencies or lockfiles"))
        if name in BUILD_FILES:
            flags.append(SafetyFlag("build_script", fc.path, "changes a build/test bootstrap file that runs in CI"))
        if guard.is_sensitive(fc.path) or (fc.previous_path and guard.is_sensitive(fc.previous_path)):
            flags.append(SafetyFlag("restricted_file", fc.path, "touches a restricted file"))
        if fc.binary:
            flags.append(SafetyFlag("binary", fc.path, "adds or changes a binary file"))
        if fc.status == "removed" and not _is_test(fc.path):
            flags.append(SafetyFlag("deletion", fc.path, "deletes a non-test file"))
        for line_no, text in fc.added:
            if len(text) > LONG_LINE or BLOB.search(text):
                flags.append(SafetyFlag("obfuscation", fc.path, "very long line or encoded blob", line_no))
            m = RISKY_CODE.search(text)
            if m and not _is_test(fc.path):
                flags.append(SafetyFlag("risky_call", fc.path, f"adds `{m.group(0).strip()}`", line_no))
            if scanner.scan(text):
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
