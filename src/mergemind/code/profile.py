"""Repo Brief: a compact, byte-stable description of the repo placed at the start of
the system prompt. Built from files only, cached per head sha."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

from mergemind.store.db import Store

MAX_README_CHARS = 2500


def _read(p: Path, limit: int = 4000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def detect_test_commands(repo: Path, files: set[str]) -> list[str]:
    cmds: list[str] = []
    pyproject = _read(repo / "pyproject.toml", 20000)
    if "pytest" in pyproject or any(f.startswith("tests/") and f.endswith(".py") for f in files) or "pytest.ini" in files:
        cmds.append("pytest")
    pkg = _read(repo / "package.json", 20000)
    if pkg:
        try:
            if "test" in json.loads(pkg).get("scripts", {}):
                cmds.append("npm test")
        except json.JSONDecodeError:
            pass
    if "go.mod" in files:
        cmds.append("go test ./...")
    if "Cargo.toml" in files:
        cmds.append("cargo test")
    if "Makefile" in files and re.search(r"^test:", _read(repo / "Makefile", 20000), re.M):
        cmds.append("make test")
    return cmds


def build_brief(repo_name: str, repo: Path, files: list[str]) -> tuple[str, dict]:
    fs = set(files)
    readme = next((f for f in ("README.md", "README.rst", "README.txt", "README") if f in fs), None)
    security = next((f for f in ("SECURITY.md", ".github/SECURITY.md", "docs/SECURITY.md") if f in fs), None)
    contributing = next((f for f in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "CONTRIBUTING.rst") if f in fs), None)
    templates = sorted(f for f in files if f.startswith(".github/ISSUE_TEMPLATE/"))
    exts = Counter(Path(f).suffix for f in files if Path(f).suffix)
    langs = ", ".join(f"{e}({n})" for e, n in exts.most_common(5))
    top_dirs = Counter(f.split("/")[0] for f in files if "/" in f)
    src_dirs = [d for d, _ in top_dirs.most_common(8) if not d.startswith(".")]
    tests = detect_test_commands(repo, fs)

    lines = [f"Repository: {repo_name}", f"Languages (by file count): {langs}",
             f"Top-level directories: {', '.join(src_dirs)}",
             f"Test commands: {', '.join(tests) or 'unknown'}",
             f"SECURITY policy: {security or 'none'}", f"CONTRIBUTING guide: {contributing or 'none'}"]
    if templates:
        lines.append("Issue templates: " + ", ".join(Path(t).name for t in templates[:6]))
    if readme:
        text = _read(repo / readme, MAX_README_CHARS * 2)
        text = re.sub(r"<[^>]+>", "", text)  # badges / html
        text = re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_README_CHARS]
        lines += ["", f"README excerpt ({readme}):", text]
    meta = {"security": security, "contributing": contributing, "test_commands": tests, "readme": readme}
    return "\n".join(lines), meta


def get_brief(store: Store, repo_name: str, repo: Path, files: list[str], sha: str) -> tuple[str, dict]:
    rid = store.repo_id(repo_name)
    row = store.conn.execute("SELECT brief, meta_json FROM repo_profiles WHERE repo_id=? AND sha=?", (rid, sha)).fetchone()
    if row:
        return row["brief"], json.loads(row["meta_json"] or "{}")
    brief, meta = build_brief(repo_name, repo, files)
    store.conn.execute("INSERT OR REPLACE INTO repo_profiles(repo_id, sha, brief, meta_json, created_at) VALUES (?,?,?,?,?)",
                       (rid, sha, brief, json.dumps(meta), time.time()))
    return brief, meta
