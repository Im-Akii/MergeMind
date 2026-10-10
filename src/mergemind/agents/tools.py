"""Read-only tools for the investigator. Every result is written to the ledger and
returned to the model with its ev_ id. Sensitive paths are never readable."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from mergemind.agents.ledger import Ledger
from mergemind.code import symbols as sym
from mergemind.code.graph import CodeIntel, CodeRegion
from mergemind.code.signals import extract
from mergemind.guard.paths import PathGuard
from mergemind.retrieval.bm25 import BM25, tokenize
from mergemind.retrieval.docs import DocsIndex
from mergemind.retrieval.dupes import IssueIndex
from mergemind.text import wrap_untrusted
from mergemind.tracing import span

MAX_READ_LINES = 200


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $ref/$defs so the tool schema is flat JSON Schema (wider endpoint support)."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


# ------------------------------------------------------------------ arguments


class RecordPlan(BaseModel):
    hypotheses: list[str] = Field(description="likely causes, most likely first")
    checks: list[str] = Field(description="what you will look at to confirm or rule them out")


class SearchCode(BaseModel):
    query: str = Field(description="error text, symbol names or a short technical description")
    top_k: int = Field(default=8, ge=1, le=15)


class ExplainTrace(BaseModel):
    text: str = Field(description="a stack trace copied from the issue")


class SymbolImpact(BaseModel):
    symbol: str = Field(description="function or method name, e.g. Editor.edit_files")
    file: str | None = Field(default=None, description="file that defines it, if known")


class SymbolDef(BaseModel):
    symbol: str


class ReadCode(BaseModel):
    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class FindSimilarIssues(BaseModel):
    query: str


class GetIssue(BaseModel):
    number: int = Field(ge=1)


class SearchDocs(BaseModel):
    query: str


DESCRIPTIONS = {
    "record_plan": "Record your hypotheses and the checks you will run. Call this first.",
    "search_code": "Search the repository for code regions relevant to a query.",
    "explain_trace": "Map a stack trace to the functions in this repository.",
    "symbol_impact": "Callers, tests and co-changed files for a function or method.",
    "symbol_def": "Find where a function, method or class is defined.",
    "read_code": f"Read lines start..end of a file (max {MAX_READ_LINES} lines).",
    "find_similar_issues": "Find earlier issues similar to a query.",
    "get_issue": "Read an earlier issue of this repository by number.",
    "search_docs": "Search the repository documentation.",
    "submit_report": "Submit the final triage report. This ends the investigation.",
}

ARG_MODELS: dict[str, type[BaseModel]] = {
    "record_plan": RecordPlan, "search_code": SearchCode, "explain_trace": ExplainTrace,
    "symbol_impact": SymbolImpact, "symbol_def": SymbolDef, "read_code": ReadCode,
    "find_similar_issues": FindSimilarIssues, "get_issue": GetIssue, "search_docs": SearchDocs,
}


@dataclass
class ToolBox:
    repo: Path
    files: list[str]
    intel: CodeIntel
    ledger: Ledger
    guard: PathGuard
    docs: DocsIndex
    issues: IssueIndex | None = None
    issue_number: int = 0
    issue_created_at: str = "9999"
    plan: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------- dispatch

    def schemas(self, report_model: type[BaseModel]) -> list[dict[str, Any]]:
        out = []
        for name, model in [*ARG_MODELS.items(), ("submit_report", report_model)]:
            out.append({"type": "function", "function": {
                "name": name, "description": DESCRIPTIONS[name],
                "parameters": inline_refs(model.model_json_schema())}})
        return out

    def dispatch(self, name: str, raw_args: str) -> tuple[str, bool]:
        """Run a tool. Returns (text for the model, ok). Errors become messages, never exceptions."""
        started = time.monotonic()
        model = ARG_MODELS.get(name)
        if model is None:
            return f"ERROR: unknown tool `{name}`. Available: {', '.join([*ARG_MODELS, 'submit_report'])}.", False
        try:
            args = model.model_validate(json.loads(raw_args or "{}"))
        except (json.JSONDecodeError, ValidationError) as e:
            return f"ERROR: invalid arguments for {name}: {str(e)[:600]}", False
        fn: Callable[[Any], str] = getattr(self, f"_{name}")
        try:
            out = fn(args)
            ok = not out.startswith("ERROR")
        except Exception as e:  # a failing backend is reported to the model, which can adapt
            out, ok = f"ERROR: {name} failed: {type(e).__name__}: {str(e)[:300]}", False
        self.calls.append({"tool": name, "ok": ok, "ms": int((time.monotonic() - started) * 1000)})
        return out, ok

    # ------------------------------------------------------------- helpers

    def _region_line(self, r: CodeRegion, why: str = "") -> str:
        ev = self.ledger.add("code_region", f"{r.file}#L{r.start}-L{r.end}",
                             _excerpt(self.repo / r.file, r.start, r.end), score=r.score,
                             meta={"symbol": r.symbol, "reasons": r.reasons})
        reasons = "; ".join(r.reasons[:2]) or why
        return f"[{ev.id}] {r.file} L{r.start}-{r.end} {r.symbol or ''} ({reasons})"

    def _visible(self, regions: list[CodeRegion]) -> list[CodeRegion]:
        return [r for r in regions if not self.guard.is_sensitive(r.file)]

    # ---------------------------------------------------------------- tools

    def _record_plan(self, a: RecordPlan) -> str:
        self.plan = a.model_dump()
        return "Plan recorded."

    @span("RETRIEVER", "tool.search_code")
    def _search_code(self, a: SearchCode) -> str:
        regions = self._visible(self.intel.search(a.query, a.top_k))
        if not regions:
            return "No matching code. Try other identifiers or error text."
        return "\n".join(self._region_line(r) for r in regions)

    def _explain_trace(self, a: ExplainTrace) -> str:
        sig = extract(a.text, set(self.files))
        if not sig.frames:
            return "No frames from this repository were found in that text."
        lines = []
        for f in sig.frames[:6]:
            if self.guard.is_sensitive(f.file):
                continue
            s = sym.enclosing(self.repo / f.file, f.line or 0)
            r = CodeRegion(f.file, s.start, s.end, s.name, 1.0, [f"trace frame line {f.line}"]) if s else \
                CodeRegion(f.file, max(1, (f.line or 1) - 10), (f.line or 1) + 10, f.func, 1.0, [f"trace frame line {f.line}"])
            lines.append(self._region_line(r))
        head = f"exception={sig.exception} signature={sig.error_signature}"
        return head + "\n" + "\n".join(lines)

    def _symbol_impact(self, a: SymbolImpact) -> str:
        imp = self.intel.impact(a.symbol, a.file)
        callers = [c for c in imp.callers if not self.guard.is_sensitive(c.file)][:12]
        tests = self.guard.filter(imp.tests)[:10]
        ev = self.ledger.add("impact", f"{a.file or '?'}::{a.symbol}",
                             f"{len(callers)} callers, {len(tests)} tests, public_api={imp.public_api}",
                             meta={"callers": [f"{c.file}::{c.symbol}" for c in callers], "tests": tests,
                                   "co_changed": imp.co_changed[:8]})
        return (f"[{ev.id}] impact of {a.symbol}: public_api={imp.public_api}\n"
                f"callers: {', '.join(f'{c.file}::{c.symbol}' for c in callers) or 'none'}\n"
                f"tests: {', '.join(tests) or 'none'}\n"
                f"changes together with: {', '.join(f'{f}({n})' for f, n in imp.co_changed[:6]) or '-'}")

    def _symbol_def(self, a: SymbolDef) -> str:
        regions = self._visible(self.intel.definition(a.symbol))[:5]
        return "\n".join(self._region_line(r, "definition") for r in regions) or f"No definition of {a.symbol} found."

    def _read_code(self, a: ReadCode) -> str:
        path = a.path.replace("\\", "/").lstrip("./")
        if self.guard.is_sensitive(path):
            return f"ERROR: {path} is restricted and cannot be read."
        if path not in set(self.files):
            return f"ERROR: {path} is not a tracked file in this repository."
        end = min(a.end, a.start + MAX_READ_LINES - 1)
        lines = (self.repo / path).read_text(encoding="utf-8", errors="replace").splitlines()
        if a.start > len(lines):
            return f"ERROR: {path} has only {len(lines)} lines."
        chunk = lines[a.start - 1 : end]
        ev = self.ledger.add("code_region", f"{path}#L{a.start}-L{a.start + len(chunk) - 1}", "\n".join(chunk))
        numbered = "\n".join(f"{a.start + i:>5} {line}" for i, line in enumerate(chunk))
        return f"[{ev.id}] {path} L{a.start}-{a.start + len(chunk) - 1}\n{numbered}"

    def _find_similar_issues(self, a: FindSimilarIssues) -> str:
        if not self.issues:
            return "Issue index unavailable."
        pool = self.issues.all_before(self.issue_created_at, self.issue_number)
        if not pool:
            return "No earlier issues indexed."
        bm = BM25([tokenize(f"{i.title} {i.title} {i.body}") for i in pool])
        out = []
        for idx, _ in bm.top(tokenize(a.query), 5):
            i = pool[idx]
            ev = self.ledger.add("issue", f"#{i.number}", f"{i.title}\n{i.body[:600]}",
                                 meta={"state": i.state, "state_reason": i.state_reason})
            out.append(f"[{ev.id}] #{i.number} [{i.state}{'/' + i.state_reason if i.state_reason else ''}] {i.title}")
        return "\n".join(out) or "No similar issues."

    def _get_issue(self, a: GetIssue) -> str:
        i = self.issues.get(a.number) if self.issues else None
        if not i or i.created_at >= self.issue_created_at:
            return f"ERROR: issue #{a.number} is not an earlier issue of this repository."
        ev = self.ledger.add("issue", f"#{i.number}", f"{i.title}\n{i.body}",
                             meta={"state": i.state, "state_reason": i.state_reason})
        return f"[{ev.id}] " + wrap_untrusted("untrusted_issue", f"#{i.number} ({i.state}) {i.title}\n\n{i.body}")

    def _search_docs(self, a: SearchDocs) -> str:
        chunks = self.docs.search(a.query, 3)
        out = []
        for c in chunks:
            ev = self.ledger.add("doc", f"{c.path}#L{c.start_line}", c.text[:1500], meta={"heading": c.heading})
            out.append(f"[{ev.id}] {c.path} § {c.heading}\n{c.text[:700]}")
        return "\n\n".join(out) or "No matching documentation."


def _excerpt(path: Path, start: int, end: int, max_lines: int = 60) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[start - 1 : min(end, start - 1 + max_lines)])
