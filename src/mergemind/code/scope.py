"""Allowed scope for a contributor's PR (v2): the localized files and their impact, widened
with files that historically change together with the top files, and new files created next
to scoped source files. The same functions run live and in the backtest
(evals/locate_v2.py), so the measured scope is the scope MergeMind enforces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mergemind.code.graph import co_changed, is_doc, is_test
from mergemind.code.symbols import SOURCE_EXT


@dataclass(frozen=True)
class ScopeParams:
    """Tuned on the backtest's dev split (evidence/eval/locate-v2.md)."""
    top_ranked: int = 5        # top-N ranked files are always in scope
    co_change_files: int = 2   # widen around the top-M ranked files ...
    co_change_top: int = 2     # ... with their K most frequent co-changed source files ...
    co_change_min: int = 2     # ... seen together in at least this many commits


DEFAULT = ScopeParams()


def widen(scope: list[str], ranked: list[str], repo: Path, present: set[str] | None = None,
          params: ScopeParams = DEFAULT, co: dict[str, list[tuple[str, int]]] | None = None) -> list[str]:
    """`co` lets the backtest pass precomputed co-change counts; live, they come from git."""
    out: dict[str, None] = dict.fromkeys(scope)
    for f in ranked[: params.top_ranked]:
        if PurePosixPath(f).suffix in SOURCE_EXT:
            out.setdefault(f, None)
    for f in ranked[: params.co_change_files]:
        pairs = co[f] if co is not None and f in co else co_changed(repo, f, top=params.co_change_top * 4)
        added = 0
        for other, n in pairs:
            if added >= params.co_change_top or n < params.co_change_min:
                break
            if (is_test(other) or is_doc(other) or PurePosixPath(other).suffix not in SOURCE_EXT
                    or (present is not None and other not in present)):
                continue  # only source code widens the scope (never config, CI or deploy files)
            out.setdefault(other, None)
            added += 1
    return list(out)


def out_of_scope(changed: list[tuple[str, bool]], scope: list[str] | set[str], allow_new: bool = True) -> list[str]:
    """`changed` is (path, is_new_file). Tests and docs are always fine; a new source file is in
    scope when it sits in a directory that already holds a scoped source file."""
    scope = set(scope)
    dirs = {str(PurePosixPath(f).parent) for f in scope if not is_test(f) and not is_doc(f)}
    out = []
    for path, new in changed:
        if path in scope or is_test(path) or is_doc(path):
            continue
        if allow_new and new and str(PurePosixPath(path).parent) in dirs:
            continue
        out.append(path)
    return out
