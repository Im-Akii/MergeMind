"""Find function/class definitions and their line spans. Python uses `ast`; other
languages use declaration regexes with brace/indent heuristics for the end line."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SOURCE_EXT = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".kt", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".swift", ".scala",
}


@dataclass(frozen=True)
class Symbol:
    name: str  # qualified within the file, e.g. "Context.invoke"
    kind: str  # function | class | method
    start: int
    end: int

    @property
    def short(self) -> str:
        return self.name.rsplit(".", 1)[-1]


_DECL = {
    "js": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?\s+(\w+)|class\s+(\w+)|"
        r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>|(\w+)\s*\([^)]*\)\s*\{)"
    ),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)\s+(?:struct|interface)"),
    "rs": re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn\s+(\w+)|struct\s+(\w+)|enum\s+(\w+)|trait\s+(\w+)|impl(?:<[^>]*>)?\s+(?:[\w:<>]+\s+for\s+)?(\w+))"),
    "java": re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|override|suspend|open|internal)\s+)*(?:class|interface|enum|fun|[\w<>\[\],]+)\s+(\w+)\s*(?:\(|\{|<|:)"),
    "rb": re.compile(r"^\s*(?:def\s+(?:self\.)?(\w+[?!]?)|class\s+(\w+)|module\s+(\w+))"),
    "php": re.compile(r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*(?:function\s+(\w+)|class\s+(\w+))"),
    "c": re.compile(r"^(?:[\w*\s]+?)\b(\w+)\s*\([^;]*\)\s*\{?\s*$"),
}
_LANG = {
    ".js": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".jsx": "js",
    ".go": "go", ".rs": "rs", ".java": "java", ".kt": "java", ".scala": "java", ".cs": "java",
    ".swift": "java", ".rb": "rb", ".php": "php", ".c": "c", ".cc": "c", ".cpp": "c",
    ".h": "c", ".hpp": "c",
}
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "else", "do", "try", "new", "function"}


def _python_symbols(src: str) -> list[Symbol]:
    out: list[Symbol] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                kind = "class" if isinstance(child, ast.ClassDef) else ("method" if prefix else "function")
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                out.append(Symbol(name, kind, start, child.end_lineno or child.lineno))
                visit(child, f"{name}.")

    visit(ast.parse(src), "")
    return out


def _regex_symbols(lines: list[str], lang: str) -> list[Symbol]:
    pat = _DECL[lang]
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        m = pat.match(line)
        if not m:
            continue
        name = next((g for g in m.groups() if g), None)
        if name and name not in _KEYWORDS:
            starts.append((i, name))
    out: list[Symbol] = []
    for idx, (start, name) in enumerate(starts):
        end = _block_end(lines, start, lang)
        if idx + 1 < len(starts) and lang not in ("rs", "java", "js"):
            end = min(end, starts[idx + 1][0] - 1)
        out.append(Symbol(name, "function", start, max(start, end)))
    return out


def _block_end(lines: list[str], start: int, lang: str) -> int:
    if lang == "rb":
        indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
        for j in range(start, len(lines)):
            s = lines[j]
            if s.strip() == "end" and len(s) - len(s.lstrip()) == indent:
                return j + 1
        return len(lines)
    depth, opened = 0, False
    for j in range(start - 1, min(len(lines), start + 2000)):
        code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//.*$', "", lines[j])
        depth += code.count("{") - code.count("}")
        opened = opened or "{" in code
        if opened and depth <= 0:
            return j + 1
        if not opened and j > start + 3:  # no body found (declaration only)
            return start
    return min(len(lines), start + 200)


@lru_cache(maxsize=4096)
def _symbols_cached(path: str, mtime: float) -> tuple[Symbol, ...]:
    p = Path(path)
    ext = p.suffix.lower()
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    if ext in (".py", ".pyi"):
        try:
            return tuple(_python_symbols(src))
        except (SyntaxError, ValueError):
            return ()
    lang = _LANG.get(ext)
    return tuple(_regex_symbols(src.splitlines(), lang)) if lang else ()


def symbols(path: Path) -> tuple[Symbol, ...]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ()
    return _symbols_cached(str(path), mtime)


def enclosing(path: Path, line: int) -> Symbol | None:
    """The innermost symbol whose span contains `line`."""
    best: Symbol | None = None
    for s in symbols(path):
        if s.start <= line <= s.end and (best is None or s.end - s.start <= best.end - best.start):
            best = s
    return best


def find_definitions(path: Path, name: str) -> list[Symbol]:
    return [s for s in symbols(path) if s.name == name or s.short == name]
