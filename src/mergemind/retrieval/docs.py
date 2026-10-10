"""BM25 over the repo's markdown/rst docs, chunked by heading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from mergemind.guard.paths import PathGuard
from mergemind.retrieval.bm25 import BM25, tokenize

DOC_EXT = (".md", ".rst", ".txt")
_HEADING = re.compile(r"^(#{1,4} .+|[^\n]+\n[=\-~^]{3,})$", re.M)


@dataclass
class DocChunk:
    path: str
    start_line: int
    heading: str
    text: str


class DocsIndex:
    def __init__(self, repo: Path, files: list[str], guard: PathGuard | None = None, max_files: int = 400):
        guard = guard or PathGuard()
        docs = [f for f in files if f.lower().endswith(DOC_EXT) and not guard.is_sensitive(f)
                and not f.lower().startswith(("changes", "changelog", "history"))
                and "/changes" not in f.lower()][:max_files]
        self.chunks: list[DocChunk] = []
        for f in docs:
            try:
                text = (repo / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            self.chunks += _chunk(f, text)
        self.bm = BM25([tokenize(c.heading + " " + c.text) for c in self.chunks]) if self.chunks else None

    def search(self, query: str, k: int = 3) -> list[DocChunk]:
        if not self.bm:
            return []
        return [self.chunks[i] for i, _ in self.bm.top(tokenize(query), k)]


def _chunk(path: str, text: str, size: int = 1200) -> list[DocChunk]:
    lines = text.splitlines()
    out: list[DocChunk] = []
    heading, start, buf = path, 1, []
    for i, line in enumerate(lines, 1):
        is_heading = line.startswith("#") or (i < len(lines) and re.fullmatch(r"[=\-~^]{3,}", lines[i].strip() or "x"))
        if (is_heading and buf) or sum(len(b) for b in buf) > size:
            out.append(DocChunk(path, start, heading, "\n".join(buf).strip()))
            buf, start = [], i
        if is_heading:
            heading = line.lstrip("# ").strip()
        buf.append(line)
    if buf:
        out.append(DocChunk(path, start, heading, "\n".join(buf).strip()))
    return [c for c in out if len(c.text) > 40]
