"""Deterministic fault injection: MERGEMIND_FAULTS="gh.merge:429@1,llm.api:bad_json@2".

Each entry fires on the Nth call of that point (1-based), once.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


class InjectedFault(Exception):
    def __init__(self, point: str, kind: str):
        super().__init__(f"injected fault {kind} at {point}")
        self.point = point
        self.kind = kind


@dataclass
class Faults:
    plan: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    calls: Counter = field(default_factory=Counter)

    @classmethod
    def parse(cls, spec: str) -> "Faults":
        plan: dict[str, list[tuple[str, int]]] = {}
        for item in filter(None, (s.strip() for s in spec.split(","))):
            point, _, rest = item.partition(":")
            kind, _, nth = rest.partition("@")
            if not point or not kind:
                raise ValueError(f"bad fault spec: {item!r}")
            plan.setdefault(point, []).append((kind, int(nth or 1)))
        return cls(plan=plan)

    def check(self, point: str) -> str | None:
        """Count a call at `point`; return the fault kind if one is due now."""
        self.calls[point] += 1
        for kind, nth in self.plan.get(point, []):
            if nth == self.calls[point]:
                return kind
        return None


NO_FAULTS = Faults()
