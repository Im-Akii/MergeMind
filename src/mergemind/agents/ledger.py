"""Per-run evidence ledger: every tool result gets an ev_ id the model must cite."""

from __future__ import annotations

import hashlib

from mergemind.models import Evidence
from mergemind.store.db import Store


class Ledger:
    def __init__(self, store: Store | None, run_id: int | None, sha: str | None):
        self.store = store
        self.run_id = run_id
        self.sha = sha
        self.items: dict[str, Evidence] = {}

    def add(self, kind: str, locator: str, excerpt: str = "", *, score: float | None = None,
            sensitive: bool = False, meta: dict | None = None) -> Evidence:
        # deterministic across runs so cached LLM transcripts replay exactly
        ev_id = "ev_" + hashlib.sha1(f"{kind}|{locator}|{self.sha}".encode()).hexdigest()[:8]
        ev = Evidence(id=ev_id, kind=kind, locator=locator, sha=self.sha, excerpt=excerpt[:4000],
                      score=score, sensitive=sensitive, meta=meta or {})
        self.items[ev_id] = ev
        if self.store:
            self.store.add_evidence(self.run_id, ev)
        return ev

    def adopt(self, ev: Evidence) -> Evidence:
        """Register evidence produced elsewhere (e.g. localization) under this run."""
        self.items[ev.id] = ev
        if self.store:
            self.store.add_evidence(self.run_id, ev)
        return ev

    def get(self, ev_id: str) -> Evidence | None:
        return self.items.get(ev_id)

    def __contains__(self, ev_id: str) -> bool:
        return ev_id in self.items
