"""Secret detection: provider token patterns, high-entropy strings, and exact known values
(from the environment and from the repo's sensitive files)."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "github_token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "aws_access_key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aws_secret": re.compile(r"(?i)aws.{0,20}(secret|key).{0,5}[:=]\s*['\"]?[A-Za-z0-9/+=]{40}\b"),
    "gcp_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "gcp_service_account": re.compile(r"\"type\":\s*\"service_account\""),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "slack_webhook": re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"),
    "stripe_key": re.compile(r"\b[rs]k_(live|test)_[A-Za-z0-9]{20,}\b"),
    "nvidia_key": re.compile(r"\bnvapi-[A-Za-z0-9_\-]{30,}\b"),
    "openai_key": re.compile(r"\bsk-(proj-)?[A-Za-z0-9_\-]{20,}\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
    # the whole block, or to the end of the text if it was cut off
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
                              r"[\s\S]*?(?:-----END (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----|\Z)"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    "url_credentials": re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s@/]{3,}@[^\s/]+"),
    "assigned_secret": re.compile(
        r"(?i)\b[\w.\-]*(password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|"
        r"client[_\-]?secret|auth)[\w.\-]*\s*[:=]\s*['\"]?([^\s'\"`,;]{6,})"),
}

# values that look like assignments but aren't secrets
_PLACEHOLDER = re.compile(r"(?i)^(\*+|x+|<[^>]+>|\$\{?\w+\}?|%\(?\w+\)?s?|your[_\-]?\w*|changeme|example\w*|"
                          r"none|null|true|false|redacted|\[redacted\]|os\.environ.*|getenv.*|env\[.*|settings\..*|"
                          r"self\.\w+|config\.\w+|\w+\(.*|required|optional|str|string|int)$")
_TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")
_SENSITIVE_ENV = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE)")
_KV = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][\w.\-]*)\s*[:=]\s*['\"]?([^'\"\n#]+?)['\"]?\s*(?:#.*)?$", re.M)


@dataclass(frozen=True)
class SecretHit:
    kind: str
    start: int
    end: int


def shannon_entropy(s: str) -> float:
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values()) if n else 0.0


def _looks_random(tok: str) -> bool:
    if len(tok) < 24 or tok.isalpha() or tok.isdigit():
        return False
    if re.fullmatch(r"[0-9a-f]{40}", tok) or re.fullmatch(r"[0-9a-f]{7,40}", tok):
        return False  # git SHAs are fine to show
    classes = sum(bool(re.search(p, tok)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
    return classes >= 3 and shannon_entropy(tok) >= 4.0


class SecretScanner:
    def __init__(self, known_values: set[str] | None = None):
        self.known = {v for v in (known_values or set()) if len(v) >= 6}

    @classmethod
    def from_environment(cls, extra: set[str] | None = None) -> "SecretScanner":
        vals = {v.strip() for k, v in os.environ.items() if _SENSITIVE_ENV.search(k) and v and len(v.strip()) >= 8}
        return cls(vals | (extra or set()))

    def learn_file(self, path: Path) -> None:
        """Every value assigned in a sensitive file (e.g. deploy/prod.env) becomes a known secret."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            return
        for _, val in _KV.findall(text):
            val = val.strip()
            if len(val) >= 6 and not _PLACEHOLDER.match(val) and not val.isdigit():
                self.known.add(val)

    def scan(self, text: str) -> list[SecretHit]:
        hits: list[SecretHit] = []
        for kind, pat in PATTERNS.items():
            for m in pat.finditer(text):
                if kind == "assigned_secret":
                    val = m.group(2)
                    if _PLACEHOLDER.match(val) or not (any(c.isdigit() for c in val) or len(val) >= 12):
                        continue
                    hits.append(SecretHit(kind, m.start(2), m.end(2)))
                else:
                    hits.append(SecretHit(kind, m.start(), m.end()))
        for m in _TOKENISH.finditer(text):
            if _looks_random(m.group()):
                hits.append(SecretHit("high_entropy", m.start(), m.end()))
        for val in self.known:
            start = text.find(val)
            while start != -1:
                hits.append(SecretHit("known_secret", start, start + len(val)))
                start = text.find(val, start + 1)
        return _merge(hits)

    def redact(self, text: str) -> tuple[str, list[SecretHit]]:
        hits = self.scan(text)
        for h in sorted(hits, key=lambda h: -h.start):
            text = text[: h.start] + "[REDACTED]" + text[h.end :]
        return text, hits


def _merge(hits: list[SecretHit]) -> list[SecretHit]:
    out: list[SecretHit] = []
    for h in sorted(hits, key=lambda h: (h.start, -h.end)):
        if out and h.start < out[-1].end:
            last = out[-1]
            out[-1] = SecretHit(last.kind, last.start, max(last.end, h.end))
        else:
            out.append(h)
    return out
