"""Code intelligence: entire-graph when installed, lexical (git grep + symbols) otherwise.

Both backends return the same `CodeRegion` shape, so callers never care which ran.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mergemind.code import symbols as sym
from mergemind.code.repo_cache import git
from mergemind.tracing import span

log = logging.getLogger(__name__)

TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__|testing|fixtures?)(/|$)|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$", re.I)
DOC_PATH = re.compile(r"(^|/)(docs?|examples?)/|\.(md|rst|txt)$|(^|/)CHANGE", re.I)


@dataclass
class CodeRegion:
    file: str
    start: int
    end: int
    symbol: str | None = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    backend: str = "lexical"

    @property
    def key(self) -> tuple[str, str | None, int]:
        return (self.file, self.symbol, self.start)


@dataclass
class Impact:
    symbol: str
    file: str
    callers: list[CodeRegion] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    co_changed: list[tuple[str, int]] = field(default_factory=list)
    public_api: bool = False


class CodeIntel(Protocol):
    name: str

    def search(self, query: str, top_k: int = 10) -> list[CodeRegion]: ...
    def definition(self, symbol: str) -> list[CodeRegion]: ...
    def impact(self, symbol: str, file: str | None = None) -> Impact: ...


def is_test(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def is_doc(path: str) -> bool:
    return bool(DOC_PATH.search(path))


# ------------------------------------------------------------------ tokenization

_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_QUERY_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "it", "be",
    "when", "this", "that", "not", "does", "doesn", "don", "should", "error", "bug", "issue",
    "fails", "failed", "fail", "work", "working", "using", "use", "get", "set", "can", "cannot",
    "after", "before", "from", "into", "but", "are", "was", "has", "have", "how", "why", "what",
    "instead", "expected", "actual", "behavior", "behaviour", "throws", "raises", "windows",
    "its", "see", "also", "any", "all", "some", "here", "there", "then", "than", "which", "who",
    "will", "would", "could", "may", "might", "must", "just", "only", "very", "more", "most",
    "other", "same", "such", "each", "both", "one", "two", "new", "old", "now", "like", "via",
    "seems", "seem", "happens", "happen", "try", "tried", "trying", "run", "running", "ran",
    "able", "unable", "time", "times", "way", "thing", "things", "something", "anything",
    "they", "them", "their", "you", "your", "our", "his", "her", "him", "she", "was", "were",
    "been", "being", "did", "done", "doing", "make", "made", "makes", "need", "needs", "want",
    "application", "app", "code", "file", "files", "line", "lines", "value", "values", "call",
    "called", "calls", "function", "method", "class", "module", "package", "library", "version",
    "python", "repro", "reproduce", "steps", "example", "output", "result", "results", "returns",
    "win", "err", "exc", "msg", "str", "int", "obj", "arg", "args", "kwargs", "self", "none", "true", "false",
}


def query_terms(query: str) -> list[str]:
    """Whole identifiers first (exact matches matter most), then their sub-words."""
    whole = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query) if w.lower() not in _QUERY_STOP]
    parts: list[str] = []
    for w in whole:
        parts += [p.lower() for p in _SPLIT.findall(w) if len(p) > 2 and p.lower() not in _QUERY_STOP]
    seen: dict[str, None] = {}
    for t in whole + parts:
        seen.setdefault(t, None)
    return list(seen)[:12]


# ---------------------------------------------------------------- lexical backend


class LexicalIntel:
    name = "lexical"

    def __init__(self, repo_path: Path):
        self.path = Path(repo_path)
        self._files: list[str] | None = None

    @property
    def files(self) -> list[str]:
        if self._files is None:
            self._files = [f for f in git(self.path, "ls-files").splitlines() if f]
        return self._files

    def _grep(self, terms: list[str], word: bool = False) -> dict[str, list[tuple[str, int]]]:
        """term -> [(file, line)] over source files."""
        hits: dict[str, list[tuple[str, int]]] = defaultdict(list)
        globs = [f"*{e}" for e in sym.SOURCE_EXT]
        for term in terms:
            args = ["grep", "-n", "-I", "-F", "-i"] + (["-w"] if word else []) + ["-e", term, "--", *globs]
            out = git(self.path, *args, check=False, timeout=60)
            for line in out.splitlines()[:5000]:
                f, _, rest = line.partition(":")
                num, _, _ = rest.partition(":")
                if num.isdigit():
                    hits[term].append((f, int(num)))
        return hits

    @span("RETRIEVER", "search_code")
    def search(self, query: str, top_k: int = 10) -> list[CodeRegion]:
        terms = query_terms(query)
        if not terms:
            return []
        hits = self._grep(terms)
        n_files = max(1, len(self.files))
        scores: dict[tuple, float] = defaultdict(float)
        regions: dict[tuple, CodeRegion] = {}
        matched_terms: dict[tuple, set[str]] = defaultdict(set)
        for term, locs in hits.items():
            df = len({f for f, _ in locs})
            idf = math.log(1 + n_files / (1 + df))
            exact = term in query and not term.islower() or "_" in term
            for f, line in locs:
                s = sym.enclosing(self.path / f, line)
                region = (
                    CodeRegion(f, s.start, s.end, s.name) if s else CodeRegion(f, max(1, line - 10), line + 10)
                )
                k = region.key
                regions.setdefault(k, region)
                bonus = 1.0
                if s and term.lower() in (s.short.lower(), s.name.lower()):
                    bonus = 4.0  # the symbol *is* the term
                elif s and term.lower() in s.name.lower():
                    bonus = 2.0
                weight = idf * bonus * (1.5 if exact else 1.0)
                # diminishing returns for repeated hits of one term in one region
                scores[k] += weight / (1 + 0.5 * (term in matched_terms[k]))
                matched_terms[k].add(term)
        out: list[CodeRegion] = []
        for k, region in regions.items():
            coverage = len(matched_terms[k]) / len(terms)
            region.score = scores[k] * (0.5 + coverage)
            if is_test(region.file) or is_doc(region.file):
                region.score *= 0.4
            region.reasons = [f"matches {', '.join(sorted(matched_terms[k])[:4])}"]
            out.append(region)
        out.sort(key=lambda r: -r.score)
        return out[:top_k]

    def definition(self, symbol: str) -> list[CodeRegion]:
        short = symbol.rsplit(".", 1)[-1]
        out: list[CodeRegion] = []
        for f, _ in self._grep([short], word=True).get(short, []):
            for s in sym.find_definitions(self.path / f, short):
                r = CodeRegion(f, s.start, s.end, s.name, 1.0, ["definition"])
                if r.key not in {o.key for o in out}:
                    out.append(r)
        # prefer exact qualified match, then non-test files
        out.sort(key=lambda r: (r.symbol != symbol, is_test(r.file)))
        return out

    @span("TOOL", "symbol_impact")
    def impact(self, symbol: str, file: str | None = None) -> Impact:
        short = symbol.rsplit(".", 1)[-1]
        imp = Impact(symbol=symbol, file=file or "")
        callers: dict[tuple, CodeRegion] = {}
        tests: set[str] = set()
        for f, line in self._grep([short], word=True).get(short, []):
            s = sym.enclosing(self.path / f, line)
            if s and s.short == short and (file is None or f == file):
                continue  # the definition itself
            if is_test(f):
                tests.add(f)
                continue
            r = CodeRegion(f, s.start, s.end, s.name) if s else CodeRegion(f, line, line)
            r.reasons = [f"calls {short}"]
            # overloads/stubs share a name: one entry per (file, symbol)
            callers.setdefault((r.file, r.symbol), r)
        imp.callers = list(callers.values())[:30]
        if file:
            stem = Path(file).stem.lower()
            tests |= {f for f in self.files if is_test(f) and stem in f.lower()}
            imp.co_changed = co_changed(self.path, file)
        imp.tests = sorted(tests)[:20]
        imp.public_api = not short.startswith("_") and bool(file) and _exported(self.path, file, short)
        return imp


def _exported(repo: Path, file: str, name: str) -> bool:
    """Python: name re-exported from a package __init__ or listed in __all__."""
    p = Path(file)
    if p.suffix != ".py":
        return not name.startswith("_")
    init = repo / p.parent / "__init__.py"
    try:
        text = init.read_text(encoding="utf-8", errors="replace") if init.exists() else ""
    except OSError:
        return False
    return bool(re.search(rf"\b{re.escape(name)}\b", text))


def co_changed(repo: Path, file: str, max_commits: int = 200, top: int = 8) -> list[tuple[str, int]]:
    """Files that most often change in the same commits as `file`."""
    out = git(repo, "log", f"-n{max_commits}", "--format=%x1e", "--name-only", "--full-diff", "--", file, check=False)
    counts: Counter = Counter()
    for block in out.split(""):
        names = {n.strip() for n in block.splitlines() if n.strip()}
        if file not in names or len(names) > 30:  # skip mass refactors
            continue
        counts.update(n for n in names if n != file)
    return counts.most_common(top)


# ----------------------------------------------------------------- entire backend


_CO_CHANGE_COUNT = re.compile(r"(\d+) recent commits?")


def entire_binary() -> str | None:
    """The entire CLI: $ENTIRE_BIN, PATH, or the installer's default location."""
    import os

    if os.environ.get("ENTIRE_BIN") and Path(os.environ["ENTIRE_BIN"]).exists():
        return os.environ["ENTIRE_BIN"]
    found = shutil.which("entire")
    if found:
        return found
    # the Windows installer updates the user PATH, which already-open terminals don't see
    for cand in (Path.home() / ".local" / "bin" / "entire.exe", Path.home() / ".local" / "bin" / "entire"):
        if cand.exists():
            return str(cand)
    return None


class EntireIntel:
    """entire-graph (`entire graph …`, plugin v0.4) wrapper. Any failure falls back to the
    lexical backend, so a missing plugin or a timeout only costs precision."""

    name = "entire"

    def __init__(self, repo_path: Path, fallback: LexicalIntel, binary: str, timeout: float = 120,
                 faults: Any = None):
        from mergemind.faults import NO_FAULTS

        self.path = Path(repo_path)
        self.fallback = fallback
        self.bin = binary
        self.timeout = timeout
        self.faults = faults or NO_FAULTS

    @staticmethod
    def available() -> bool:
        return entire_binary() is not None

    def _run(self, *args: str) -> Any:
        fault = self.faults.check(f"graph.{args[0]}")
        if fault:
            from mergemind import tracing

            tracing.attrs(injected_fault=f"graph.{args[0]}:{fault}", recovery="lexical_fallback")
            if fault == "timeout":
                raise subprocess.TimeoutExpired(cmd="entire graph", timeout=self.timeout)
            raise RuntimeError(f"injected {fault}")
        proc = subprocess.run(
            # --head: query the committed tree, which reuses the cached index (our checkouts are clean commits)
            [self.bin, "graph", *args, "--repo", str(self.path), "--format", "json", "--head"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:300] or f"exit {proc.returncode}")
        return json.loads(proc.stdout)

    def index(self) -> None:
        """Warm the graph cache so later queries answer in well under a second."""
        subprocess.run([self.bin, "graph", "index", "--repo", str(self.path), "--format", "json"],
                       capture_output=True, timeout=1800, check=False)

    @staticmethod
    def _region(it: dict, reasons: list[str] | None = None) -> CodeRegion | None:
        f = it.get("file_path")
        if not f:
            return None  # external symbols (stdlib, dependencies) have no file
        start = int(it.get("start_line") or 1)
        end = int(it.get("end_line") or start)
        return CodeRegion(f.replace("\\", "/"), start, end, it.get("qualified_name") or it.get("symbol_name")
                          or it.get("name"), float(it.get("score") or 0), reasons or [], "entire")

    @span("RETRIEVER", "search_code")
    def search(self, query: str, top_k: int = 10) -> list[CodeRegion]:
        try:
            data = self._run("search", "--query", query, "--top-k", str(top_k), "--profile", "full")
            out = []
            for it in data.get("results", []):
                r = self._region(it, [str(s) for s in it.get("signals", [])][:4])
                if r:
                    out.append(r)
            return out[:top_k]
        except Exception as e:
            log.warning("entire search failed (%s); lexical fallback", e)
            return self.fallback.search(query, top_k)

    def definition(self, symbol: str) -> list[CodeRegion]:
        try:
            data = self._run("def", symbol)
            out = [r for r in (self._region(d, ["definition"]) for d in data.get("declarations", [])) if r]
            # only exact name matches: a lookup for a non-symbol ("notepad.exe") can return loose hits
            want = symbol.rsplit(".", 1)[-1]
            out = [r for r in out if r.symbol and r.symbol.rsplit(".", 1)[-1] == want]
            # entire also indexes prose (markdown headings are "symbols"); a code name means code
            code = [r for r in out if not is_doc(r.file)]
            out = code or out
            out.sort(key=lambda r: (r.symbol != symbol, is_test(r.file)))
            return out
        except Exception as e:
            log.warning("entire def failed (%s); lexical fallback", e)
            return self.fallback.definition(symbol)

    @span("TOOL", "symbol_impact")
    def impact(self, symbol: str, file: str | None = None) -> Impact:
        try:
            args = ["impact", "--symbol", symbol]
            if file:
                args += ["--file", file]
            data = self._run(*args)
            if data.get("disambiguation_required"):
                raise RuntimeError(f"ambiguous symbol {symbol}")
            imp = Impact(symbol=symbol, file=file or (data.get("focus") or {}).get("file_path", ""))
            seen: set[tuple] = set()
            tests: list[str] = []
            for e in (data.get("callers") or {}).get("entries", []):
                r = self._region(e.get("endpoint") or {}, [f"calls {symbol.rsplit('.', 1)[-1]} (depth {e.get('depth')})"])
                if r is None or (r.file, r.symbol) in seen:
                    continue
                seen.add((r.file, r.symbol))
                if is_test(r.file):
                    tests.append(r.file)
                else:
                    imp.callers.append(r)
            # tests often reach code through fixtures the call graph can't see; add test files
            # named after the module (same rule as the lexical backend)
            if imp.file:
                stem = Path(imp.file).stem.lower()
                tests += sorted(f for f in self.fallback.files if is_test(f) and stem in f.lower())
            imp.tests = list(dict.fromkeys(tests))[:20]
            for e in (data.get("co_changes") or {}).get("entries", []):
                f = (e.get("endpoint") or {}).get("file_path")
                m = _CO_CHANGE_COUNT.search(e.get("detail") or "")
                if f:
                    imp.co_changed.append((f, int(m.group(1)) if m else 1))
            imp.public_api = bool(imp.file) and not symbol.rsplit(".", 1)[-1].startswith("_") \
                and _exported(self.path, imp.file, symbol.rsplit(".", 1)[-1])
            return imp
        except Exception as e:
            log.warning("entire impact failed (%s); lexical fallback", e)
            return self.fallback.impact(symbol, file)


def code_intel(repo_path: Path, prefer_entire: bool = True, faults: Any = None) -> CodeIntel:
    import os

    lexical = LexicalIntel(repo_path)
    # MERGEMIND_NO_ENTIRE=1 forces the lexical backend (tests, ablations)
    binary = entire_binary() if prefer_entire and not os.environ.get("MERGEMIND_NO_ENTIRE") else None
    if binary:
        return EntireIntel(repo_path, lexical, binary, faults=faults)
    return lexical
