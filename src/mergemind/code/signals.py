"""Plain-code signal extraction from issue text: stack traces, paths, identifiers, errors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass
class Frame:
    file: str
    line: int | None
    func: str | None


@dataclass
class Signals:
    frames: list[Frame] = field(default_factory=list)
    exception: str | None = None
    error_signature: str | None = None
    paths: list[str] = field(default_factory=list)  # verified against the repo
    identifiers: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    issue_refs: list[int] = field(default_factory=list)
    has_trace: bool = False


# --- stack trace patterns ---------------------------------------------------
_PY_FRAME = re.compile(r'File "([^"]+)", line (\d+), in ([\w<>.]+)')
_PY_EXC = re.compile(r"^\s*([A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt))(?::\s*(.*))?$", re.M)
_JS_FRAME = re.compile(r"at (?:([\w$.<>]+) )?\(?((?:[A-Za-z]:)?[^\s():]+\.(?:[cm]?js|tsx?|jsx)):(\d+)(?::\d+)?\)?")
_JAVA_FRAME = re.compile(r"at ([\w$.]+)\.([\w$<>]+)\(([\w$]+\.(?:java|kt|scala)):(\d+)\)")
_GO_FRAME = re.compile(r"^\s*((?:[A-Za-z]:)?[\w./\\-]+\.go):(\d+)", re.M)
_GO_FUNC = re.compile(r"^([\w./*()-]+)\(.*\)$", re.M)
_RUST_FRAME = re.compile(r"at ((?:[A-Za-z]:)?[\w./\\-]+\.rs):(\d+)(?::\d+)?")
_RUST_FUNC = re.compile(r"^\s*\d+:\s+([\w:<>]+)$", re.M)

_CODE_SPAN = re.compile(r"`([^`\n]{2,120})`")
_PATHLIKE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|go|rs|java|kt|rb|php|c|cc|cpp|h|hpp|cs|swift|scala|toml|ya?ml|json|cfg|ini|md|rst))(?::(\d+))?")
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?:\(\))?")
_QUOTED_ERR = re.compile(r"""(?:error|Error|ERROR|exception|failed|Failed)[:\s]+["']?([^"'\n]{8,160})""")
_VERSION = re.compile(r"\b(?:v|version\s*)?(\d+\.\d+(?:\.\d+)?(?:[-.]?(?:rc|a|b|dev|post)\d*)?)\b", re.I)
_ISSUE_REF = re.compile(r"(?<![\w/])#(\d{1,7})\b")

_STOP = {
    "None", "True", "False", "self", "this", "null", "undefined", "error", "Error", "the",
    "and", "or", "not", "def", "class", "return", "import", "from", "print", "if", "else",
    "pip", "python", "install", "http", "https", "www", "com", "github",
}


def _norm_path(p: str) -> str:
    return p.replace("\\", "/")


def _repo_relative(path: str, repo_files: set[str]) -> str | None:
    """Map an absolute/site-packages path from a trace to a file in the repo."""
    path = _norm_path(path)
    if path in repo_files:
        return path
    parts = PurePosixPath(path).parts
    # longest suffix that exists in the repo wins
    for i in range(len(parts)):
        cand = "/".join(parts[i:])
        if cand in repo_files:
            return cand
    return None


def extract(text: str, repo_files: set[str] | None = None) -> Signals:
    repo_files = repo_files or set()
    sig = Signals()
    text = text or ""

    # stack traces
    frames: list[Frame] = []
    for m in _PY_FRAME.finditer(text):
        frames.append(Frame(m.group(1), int(m.group(2)), m.group(3)))
    for m in _JS_FRAME.finditer(text):
        frames.append(Frame(m.group(2), int(m.group(3)), m.group(1)))
    for m in _JAVA_FRAME.finditer(text):
        frames.append(Frame(m.group(3), int(m.group(4)), f"{m.group(1)}.{m.group(2)}"))
    for m in _GO_FRAME.finditer(text):
        frames.append(Frame(m.group(1), int(m.group(2)), None))
    for m in _RUST_FRAME.finditer(text):
        frames.append(Frame(m.group(1), int(m.group(2)), None))
    sig.has_trace = bool(frames)

    # keep frames that land inside the repo, deepest (closest to the error) first
    in_repo: list[Frame] = []
    for f in frames:
        rel = _repo_relative(f.file, repo_files) if repo_files else None
        if rel:
            in_repo.append(Frame(rel, f.line, f.func))
    # Python prints the innermost frame last; JS/Java/Go/Rust print it first
    is_python = bool(_PY_FRAME.search(text))
    sig.frames = list(reversed(in_repo)) if is_python else in_repo

    exc = _PY_EXC.findall(text)
    if exc:
        sig.exception = exc[-1][0]
        if exc[-1][1]:
            sig.error_messages.append(exc[-1][1].strip()[:160])
    if sig.exception or sig.frames:
        top = [f"{f.file}:{f.func or '?'}" for f in sig.frames[:3]]
        sig.error_signature = "|".join([sig.exception or "?", *top])

    # paths
    seen: set[str] = set()
    for m in _PATHLIKE.finditer(text):
        rel = _repo_relative(m.group(1), repo_files) if repo_files else None
        if rel and rel not in seen:
            seen.add(rel)
            sig.paths.append(rel)
    for f in sig.frames:
        if f.file not in seen:
            seen.add(f.file)
            sig.paths.append(f.file)

    # identifiers: backticked spans and trace function names
    idents: list[str] = []
    for span in _CODE_SPAN.findall(text):
        for m in _IDENT.finditer(span):
            idents.append(m.group(1))
    idents += [f.func for f in sig.frames if f.func and not f.func.startswith("<")]
    # CamelCase / snake_case words in prose are often symbol names too
    idents += re.findall(r"\b([a-z]+_[a-z0-9_]+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b", text)
    out: list[str] = []
    for ident in idents:
        ident = ident.strip(".")
        if len(ident) < 3 or ident in _STOP or ident.lower() in _STOP or ident in out:
            continue
        if re.fullmatch(r"\d+(\.\d+)*", ident):
            continue
        out.append(ident)
    sig.identifiers = out[:25]

    for m in _QUOTED_ERR.finditer(text):
        msg = m.group(1).strip().rstrip(".")
        if msg not in sig.error_messages:
            sig.error_messages.append(msg)
    sig.error_messages = sig.error_messages[:5]
    sig.versions = list(dict.fromkeys(m.group(1) for m in _VERSION.finditer(text)))[:5]
    sig.issue_refs = list(dict.fromkeys(int(n) for n in _ISSUE_REF.findall(text)))[:10]
    return sig
