"""Leak guard (buildplan-v2 §7.6): every message a contributor can see passes through here.

Order: mention escaping → secret redaction → restricted / out-of-scope paths → code blocks
from restricted or out-of-scope files → sensitive evidence → security wording (hold) →
LLM critic (hold). Every decision is logged to guard_log and traced as a GUARDRAIL span.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from mergemind.guard.paths import PathGuard
from mergemind.guard.secrets import SecretScanner
from mergemind.llm import LLM, LLMError
from mergemind.store.db import Store
from mergemind.tracing import span

Decision = Literal["passed", "redacted", "held_for_maintainer"]

_MENTION = re.compile(r"(?<![\w`/])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\b")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_GH_LINK = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+/(?:blob|tree|raw)/[^/\s]+/([^\s#?)\]]+)(?:#L\d+(?:-L\d+)?)?")
_PATHLIKE = re.compile(r"(?<![\w.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,6}|(?:[\w.-]+/)+[\w.-]+)")
_SECURITY = re.compile(
    r"(?i)\b(vulnerab\w*|exploit\w*|CVE-\d{4}-\d+|remote code execution|RCE|SQL injection|SQLi|XSS|"
    r"cross[- ]site|auth(?:entication)? bypass|privilege escalation|path traversal|SSRF|CSRF|"
    r"injection attack|attack(?:er)? could)\b")
MIN_INDEX_LINE = 16
MAX_INDEX_FILE_BYTES = 1_000_000

CRITIC_PROMPT_VERSION = "leak-critic-v1"
CRITIC_SYSTEM = """You review a message that an automated assistant is about to post to an outside contributor
who is fixing one specific GitHub issue. Flag the message only if it reveals something the contributor
does not need for THAT issue, such as: secrets or credentials, contents of configuration/deployment files,
security weaknesses, internal or private discussion, personal data about other people, or code and details
from unrelated parts of the system. Code, file names and explanations about the issue's own area are fine.
MergeMind's own process text is always fine and never a reason to flag: the access deadline, how to fork,
branch and open a pull request, how checks, maintainer approval and merging work, how to withdraw, test
commands, and lists of files in the issue's scope."""


class CriticResult(BaseModel):
    reveals_extra: bool
    items: list[str] = Field(default_factory=list, description="what is revealed that is not needed")


@dataclass
class GuardResult:
    decision: Decision
    text: str
    findings: list[dict] = field(default_factory=list)

    @property
    def postable(self) -> bool:
        return self.decision != "held_for_maintainer"


class LeakGuard:
    def __init__(self, repo: Path | None, files: list[str], paths: PathGuard, scanner: SecretScanner,
                 llm: LLM | None = None, store: Store | None = None):
        self.repo = repo
        self.files = files
        self.fileset = set(files)
        self.paths = paths
        self.scanner = scanner
        self.llm = llm
        self.store = store
        self._line_index: dict[str, set[str]] | None = None

    @classmethod
    def build(cls, repo: Path | None, files: list[str], sensitive_patterns: list[str],
              llm: LLM | None = None, store: Store | None = None,
              extra_secrets: set[str] | None = None) -> "LeakGuard":
        paths = PathGuard(sensitive_patterns)
        scanner = SecretScanner.from_environment(extra_secrets)
        if repo is not None:
            for f in files:
                if paths.is_sensitive(f):
                    scanner.learn_file(repo / f)
        return cls(repo, files, paths, scanner, llm, store)

    # ------------------------------------------------------------- index

    def _index(self) -> dict[str, set[str]]:
        """normalized source line -> files containing it (for attributing pasted code)."""
        if self._line_index is None:
            idx: dict[str, set[str]] = defaultdict(set)
            if self.repo is not None:
                for f in self.files:
                    p = self.repo / f
                    try:
                        if p.stat().st_size > MAX_INDEX_FILE_BYTES:
                            continue
                        text = p.read_text(encoding="utf-8", errors="strict")
                    except (OSError, UnicodeDecodeError):
                        continue
                    for line in text.splitlines():
                        n = _norm(line)
                        if len(n) >= MIN_INDEX_LINE:
                            idx[n].add(f)
            self._line_index = idx
        return self._line_index

    # ------------------------------------------------------------- check

    @span("GUARDRAIL", "leak_guard")
    def check(
        self,
        text: str,
        *,
        kind: str,
        allowed_files: set[str] | None = None,
        allowed_mentions: set[str] | None = None,
        context: str = "",
        sensitive_excerpts: list[str] | None = None,
        use_critic: bool = True,
        run_id: int | None = None,
    ) -> GuardResult:
        findings: list[dict] = []
        out = text
        allowed_mentions = {m.lower() for m in (allowed_mentions or set())}

        # 1. mentions: only explicitly allowed people can be pinged
        def _mention(m: re.Match) -> str:
            if m.group(1).lower() in allowed_mentions:
                return m.group(0)
            findings.append({"type": "mention", "login": m.group(1)})
            return f"`{m.group(1)}`"

        out = _MENTION.sub(_mention, out)

        # 2. code blocks from restricted or out-of-scope files (before secret redaction,
        #    so a pasted restricted file is still recognised and removed whole)
        out = _FENCE.sub(lambda m: self._check_block(m, allowed_files, findings), out)

        # 3. secrets
        out, hits = self.scanner.redact(out)
        findings += [{"type": "secret", "kind": h.kind} for h in hits]

        # 4. paths: restricted always; out-of-scope when a scope is given
        out = self._check_paths(out, allowed_files, findings)

        # 5. sensitive evidence (security notes, maintainer-only findings, CI env)
        for ex in sensitive_excerpts or []:
            for line in ex.splitlines():
                n = line.strip()
                if len(n) >= 20 and n in out:
                    out = out.replace(n, "[removed: maintainer-only detail]")
                    findings.append({"type": "sensitive_evidence"})

        decision: Decision = "redacted" if out != text else "passed"

        # 6. security wording can't be safely auto-edited: hold
        if _SECURITY.search(out):
            findings.append({"type": "security_wording", "match": _SECURITY.search(out).group(0)})
            decision = "held_for_maintainer"

        # 7. LLM critic on what is left
        if decision != "held_for_maintainer" and use_critic and self.llm is not None:
            crit = self._critic(out, context)
            if crit is not None and crit.reveals_extra:
                findings.append({"type": "critic", "items": crit.items[:5]})
                decision = "held_for_maintainer"

        self._log(run_id, kind, decision, findings)
        from mergemind import tracing

        tracing.attrs(message_kind=kind, decision=decision, finding_types=[f.get("type") for f in findings])
        return GuardResult(decision, out, findings)

    def _check_block(self, m: re.Match, allowed: set[str] | None, findings: list[dict]) -> str:
        body = m.group(1)
        lines = [_norm(line) for line in body.splitlines()]
        lines = [line for line in lines if len(line) >= MIN_INDEX_LINE]
        if not lines:
            return m.group(0)
        idx = self._index()
        sensitive = out_of_scope = known = 0
        for line in lines:
            owners = idx.get(line)
            if not owners:
                continue
            known += 1
            if any(self.paths.is_sensitive(f) for f in owners):
                sensitive += 1
            elif allowed is not None and not owners & allowed:
                out_of_scope += 1
        if sensitive:
            findings.append({"type": "restricted_code", "lines": sensitive})
            return "```\n[removed: content from a restricted file]\n```"
        if out_of_scope >= 2 and out_of_scope >= 0.3 * len(lines):
            findings.append({"type": "out_of_scope_code", "lines": out_of_scope})
            return "```\n[removed: code from outside this issue's scope]\n```"
        return m.group(0)

    def _path_verdict(self, p: str, allowed: set[str] | None, findings: list[dict], strict: bool) -> str | None:
        """Replacement text if `p` must not appear, else None."""
        p = p.removeprefix("./").replace("\\", "/")
        if self.paths.is_sensitive(p) and (strict or p in self.fileset or "/" in p or p.startswith(".env")):
            findings.append({"type": "restricted_path"})
            return "[restricted file]"
        if allowed is not None and p in self.fileset and p not in allowed:
            findings.append({"type": "out_of_scope_path", "path": p})
            return "[a file outside this issue's scope]"
        return None

    def _check_paths(self, text: str, allowed: set[str] | None, findings: list[dict]) -> str:
        # GitHub file links: judge the path inside the link
        text = _GH_LINK.sub(lambda m: self._path_verdict(m.group(1), allowed, findings, strict=True) or m.group(0), text)
        # set every remaining URL aside so its parts aren't read as file paths
        urls: list[str] = []

        def stash(m: re.Match) -> str:
            urls.append(m.group(0))
            return f"\x00{len(urls) - 1}\x00"

        text = re.sub(r"https?://\S+", stash, text)
        text = _PATHLIKE.sub(lambda m: self._path_verdict(m.group(1), allowed, findings, strict=False) or m.group(1), text)
        return re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], text)

    def _critic(self, text: str, context: str) -> CriticResult | None:
        from mergemind import prompts

        system, pv = prompts.get("leak_critic", CRITIC_SYSTEM)
        try:
            res, _ = self.llm.structured(
                CriticResult, stage="leak_critic", model=self.llm.settings.llm_model_fast,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": f"# The issue the contributor is fixing\n{context[:3000]}\n\n"
                                                      f"# Message to be posted\n{text[:6000]}"}],
                prompt_version=pv, max_tokens=800)
            return res
        except LLMError:
            # fail closed: if we can't check, a human looks first
            return CriticResult(reveals_extra=True, items=["leak critic unavailable"])

    def _log(self, run_id: int | None, kind: str, decision: str, findings: list[dict]) -> None:
        if self.store is None:
            return
        self.store.conn.execute(
            "INSERT INTO guard_log(run_id, message_kind, decision, findings_json, created_at) VALUES (?,?,?,?,?)",
            (run_id, kind, decision, json.dumps(findings), time.time()))


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())
