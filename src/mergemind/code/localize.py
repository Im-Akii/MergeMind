"""Issue → code regions: several independent queries fused with Reciprocal Rank Fusion,
then an impact set that becomes the issue's allowed scope (buildplan-v2 §7.3)."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from mergemind.code import symbols as sym
from mergemind.code.graph import CodeIntel, CodeRegion, Impact, is_doc, is_test
from mergemind.code.signals import Signals
from mergemind.models import Evidence
from mergemind.tracing import span

RRF_K = 60


@dataclass
class Localization:
    regions: list[CodeRegion]
    confidence: str  # high | medium | low
    queries: dict[str, list[CodeRegion]] = field(default_factory=dict)
    impacts: list[Impact] = field(default_factory=list)
    scope_files: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    backend: str = "lexical"


def ev_id(*parts: object) -> str:
    return "ev_" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _frame_regions(repo: Path, sig: Signals) -> list[CodeRegion]:
    out = []
    for f in sig.frames[:5]:
        if f.line:
            s = sym.enclosing(repo / f.file, f.line)
            if s:
                out.append(CodeRegion(f.file, s.start, s.end, s.name, 1.0, ["stack-trace frame"]))
                continue
            out.append(CodeRegion(f.file, max(1, f.line - 10), f.line + 10, f.func, 1.0, ["stack-trace frame"]))
    return out


def _definition_regions(intel: CodeIntel, sig: Signals) -> list[CodeRegion]:
    out: list[CodeRegion] = []
    for ident in sig.identifiers[:6]:  # later identifiers rarely matter; each lookup costs a graph query
        for r in intel.definition(ident)[:2]:
            r.reasons = [f"defines `{ident}` named in the issue"]
            out.append(r)
    return out


def _path_regions(repo: Path, sig: Signals) -> list[CodeRegion]:
    out = []
    for p in sig.paths[:5]:
        syms = sym.symbols(repo / p)
        if syms:
            top = max(syms, key=lambda s: s.end - s.start)
            out.append(CodeRegion(p, top.start, top.end, top.name, 0.5, ["file named in the issue"]))
        else:
            out.append(CodeRegion(p, 1, 50, None, 0.5, ["file named in the issue"]))
    return out


def _merge_adjacent(regions: list[CodeRegion]) -> list[CodeRegion]:
    """Collapse overlapping/nested regions in the same file, keeping the best-scored one."""
    kept: list[CodeRegion] = []
    for r in regions:  # already sorted best-first
        clash = next((k for k in kept if k.file == r.file and r.start <= k.end + 3 and k.start <= r.end + 3), None)
        if clash:
            clash.reasons = list(dict.fromkeys(clash.reasons + r.reasons))[:5]
            continue
        kept.append(r)
    return kept


@span("CHAIN", "localize")
def localize(
    intel: CodeIntel,
    repo: Path,
    title: str,
    body: str,
    sig: Signals,
    extra_queries: list[str] | None = None,
    top_k: int = 5,
    sha: str | None = None,
) -> Localization:
    queries: dict[str, list[CodeRegion]] = {}
    queries["title"] = intel.search(title, top_k=15)
    tech = " ".join([*(sig.error_messages[:2]), *(sig.identifiers[:6]), sig.exception or ""]).strip()
    if tech and tech != title:
        queries["technical"] = intel.search(tech, top_k=15)
    if sig.identifiers:
        queries["identifiers"] = _definition_regions(intel, sig)
    if sig.paths:
        queries["paths"] = _path_regions(repo, sig)
    if sig.frames:
        queries["trace"] = _frame_regions(repo, sig)
    for i, q in enumerate(extra_queries or []):
        queries[f"extra{i}"] = intel.search(q, top_k=15)

    fused: dict[tuple, float] = defaultdict(float)
    best: dict[tuple, CodeRegion] = {}
    support: dict[tuple, set[str]] = defaultdict(set)
    file_support: dict[str, set[str]] = defaultdict(set)
    for qname, results in queries.items():
        weight = 2.0 if qname == "trace" else 1.0
        for rank, r in enumerate(results):
            fused[r.key] += weight / (RRF_K + rank + 1)
            support[r.key].add(qname)
            file_support[r.file].add(qname)
            prev = best.get(r.key)
            if prev is None:
                best[r.key] = CodeRegion(r.file, r.start, r.end, r.symbol, 0, list(r.reasons), r.backend)
            else:
                prev.reasons = list(dict.fromkeys(prev.reasons + r.reasons))

    mentioned = set(sig.paths)
    about_tests = any(w in (title + body).lower() for w in ("test", "pytest", "fixture", "ci "))
    about_docs = any(w in (title + body).lower() for w in ("docs", "documentation", "typo", "readme"))
    ranked: list[CodeRegion] = []
    for k, r in best.items():
        score = fused[k]
        if r.file in mentioned:
            score *= 1.5
        if is_test(r.file) and not about_tests:
            score *= 0.35
        if is_doc(r.file) and not about_docs:
            score *= 0.35
        score *= 1 + 0.15 * (len(file_support[r.file]) - 1)  # independent queries agree on the file
        r.score = round(score * 1000, 2)
        ranked.append(r)
    ranked.sort(key=lambda r: -r.score)
    regions = _merge_adjacent(ranked)[:top_k]

    # confidence: a trace pins it, or ≥2 independent queries agree on the top file
    if regions:
        top_file_queries = file_support[regions[0].file]
        if "trace" in top_file_queries or len(top_file_queries - {"extra0"}) >= 2:
            confidence = "high"
        elif len(regions) >= 2 and regions[0].file == regions[1].file:
            confidence = "medium"
        else:
            confidence = "low"
    else:
        confidence = "low"

    loc = Localization(regions, confidence, queries, backend=getattr(intel, "name", "lexical"))
    for r in regions:
        excerpt = _excerpt(repo / r.file, r.start, r.end)
        loc.evidence.append(Evidence(
            id=ev_id("code", r.file, r.start, r.end, sha), kind="code_region",
            locator=f"{r.file}#L{r.start}-L{r.end}", sha=sha, excerpt=excerpt, score=r.score,
            meta={"symbol": r.symbol, "reasons": r.reasons},
        ))

    # impact of the top regions → allowed scope for the contributor's PR
    # only strong regions (≥ 40% of the top score) widen the scope via their impact
    strong = [r for r in regions[:3] if regions and r.score >= 0.4 * regions[0].score]
    scope: dict[str, None] = {r.file: None for r in strong}
    for r in strong:
        if not r.symbol:
            continue
        imp = intel.impact(r.symbol, r.file)
        loc.impacts.append(imp)
        for c in imp.callers[:10]:
            if not is_doc(c.file):
                scope.setdefault(c.file, None)
        for t in imp.tests[:10]:
            scope.setdefault(t, None)
        loc.evidence.append(Evidence(
            id=ev_id("impact", r.file, r.symbol, sha), kind="impact", locator=f"{r.file}::{r.symbol}", sha=sha,
            excerpt=f"{len(imp.callers)} callers, {len(imp.tests)} tests, public_api={imp.public_api}",
            meta={"callers": [f"{c.file}::{c.symbol}" for c in imp.callers[:15]], "tests": imp.tests[:15],
                  "co_changed": imp.co_changed[:8]},
        ))
    loc.scope_files = list(scope)
    return loc


# v2: entire-graph and lexical localization each win on different repos (evidence/eval/locate-v1.md),
# so both run and their file rankings are fused. Weights tuned on the dev split (locate-v2.md).
FUSE_K = 10
FUSE_WEIGHTS = {"entire": 0.75, "lexical": 1.0}


def fuse_files(ranked: dict[str, list[str]], weights: dict[str, float] | None = None, k: int | None = None) -> list[str]:
    """Weighted Reciprocal Rank Fusion of per-backend file rankings."""
    weights = weights or FUSE_WEIGHTS
    k = FUSE_K if k is None else k
    score: dict[str, float] = defaultdict(float)
    for name, files in ranked.items():
        for rank, f in enumerate(files):
            score[f] += weights.get(name, 1.0) / (k + rank + 1)
    return sorted(score, key=lambda f: -score[f])


def localize_fused(intels: dict[str, CodeIntel], repo: Path, title: str, body: str, sig: Signals,
                   top_k: int = 5, sha: str | None = None) -> Localization:
    """Run localization once per backend and fuse: regions follow the fused file order, impacts,
    evidence and scope are the union, then the scope is widened (code/scope.py)."""
    from mergemind.code.scope import widen

    locs = {name: localize(intel, repo, title, body, sig, top_k=10, sha=sha) for name, intel in intels.items()}
    if len(locs) == 1:
        (loc,) = locs.values()
        loc.scope_files = widen(loc.scope_files, list(dict.fromkeys(r.file for r in loc.regions)), repo)
        loc.regions = loc.regions[:top_k]
        return loc
    order = fuse_files({n: list(dict.fromkeys(r.file for r in lc.regions)) for n, lc in locs.items()})
    rank = {f: i for i, f in enumerate(order)}
    regions: list[CodeRegion] = []
    seen: set[tuple] = set()
    for lc in locs.values():
        for r in lc.regions:
            if r.key not in seen:
                seen.add(r.key)
                regions.append(r)
    regions.sort(key=lambda r: (rank.get(r.file, len(rank)), -r.score))
    regions = _merge_adjacent(regions)[:top_k]
    confs = [lc.confidence for lc in locs.values()]
    agree = len({next(iter(dict.fromkeys(r.file for r in lc.regions)), None) for lc in locs.values()}) == 1
    confidence = "high" if "high" in confs or (agree and regions) else ("medium" if "medium" in confs else "low")
    # region evidence must follow the region order (triage pairs them by position)
    region_ev: dict[tuple, Evidence] = {}
    for lc in locs.values():
        for r, e in zip(lc.regions, [e for e in lc.evidence if e.kind == "code_region"]):
            region_ev.setdefault(r.key, e)
    evidence = [region_ev[r.key] for r in regions if r.key in region_ev]
    evidence += list({e.id: e for lc in locs.values() for e in lc.evidence if e.kind != "code_region"}.values())
    scope = list(dict.fromkeys(f for lc in locs.values() for f in lc.scope_files))
    # one impact per symbol: callers and tests are the union of what each backend found
    impacts: dict[tuple[str, str], Impact] = {}
    for imp in (i for lc in locs.values() for i in lc.impacts):
        key = (imp.file, imp.symbol)
        if key not in impacts:
            impacts[key] = Impact(imp.symbol, imp.file, list(imp.callers), list(imp.tests), list(imp.co_changed),
                                  imp.public_api)
            continue
        m = impacts[key]
        seen_callers = {(c.file, c.symbol) for c in m.callers}
        m.callers += [c for c in imp.callers if (c.file, c.symbol) not in seen_callers]
        m.tests = list(dict.fromkeys(m.tests + imp.tests))
        m.co_changed = m.co_changed or imp.co_changed
        m.public_api = m.public_api or imp.public_api
    return Localization(
        regions, confidence, {f"{n}:{q}": rs for n, lc in locs.items() for q, rs in lc.queries.items()},
        impacts=list(impacts.values()), scope_files=widen(scope, order, repo),
        evidence=evidence, backend="+".join(locs))


def _excerpt(path: Path, start: int, end: int, max_lines: int = 40) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    chunk = lines[start - 1 : min(end, start - 1 + max_lines)]
    return "\n".join(chunk)


def difficulty(loc: Localization) -> str:
    """easy: 1 file, ≤3 callers, tests nearby, not public API; hard: cross-module or >15 callers or public API."""
    if not loc.regions:
        return "medium"
    files = {r.file for r in loc.regions[:3]}
    callers = sum(len(i.callers) for i in loc.impacts[:1])
    public = any(i.public_api for i in loc.impacts[:1])
    top_dirs = {f.split("/")[0] for f in files}
    has_tests = any(i.tests for i in loc.impacts[:1])
    if len(top_dirs) > 1 or callers > 15 or public:
        return "hard"
    if len(files) == 1 and callers <= 3 and has_tests:
        return "easy"
    return "medium"
