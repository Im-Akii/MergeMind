"""Prompt versioning through neatlogs Prompts (buildplan-v2 §10).

Each agent prompt has a local default (in code). `mergemind prompts push` saves the local
text as a new version in neatlogs; at runtime the version labelled `production` is used,
so v2 is promoted by moving the label, not by redeploying. Without neatlogs, or if the
fetch fails, the local default is used.
"""

from __future__ import annotations

import hashlib
import logging

from mergemind import tracing

log = logging.getLogger(__name__)

PREFIX = "mergemind."
_cache: dict[str, tuple[str, str]] = {}


def registry() -> dict[str, str]:
    """name -> local default text, for every prompt MergeMind sends."""
    from mergemind.agents import answer, investigator, verifier
    from mergemind.guard import leak
    from mergemind.pr import review
    from mergemind.retrieval import dupes

    return {
        "investigator": investigator.SYSTEM,
        "critic": verifier.CRITIC_SYSTEM,
        "dup_adjudicator": dupes.ADJ_SYSTEM,
        "pr_reviewer": review.SYSTEM,
        "leak_critic": leak.CRITIC_SYSTEM,
        "answerer": answer.SYSTEM,
    }


def local_version(text: str) -> str:
    return "local-" + hashlib.sha1(text.encode()).hexdigest()[:8]


def get(name: str, default: str, label: str = "production") -> tuple[str, str]:
    """(prompt text, version tag). The version tag goes into the trace and the LLM cache key."""
    if name in _cache:
        return _cache[name]
    text, version = default, local_version(default)
    if tracing.enabled():
        try:
            import neatlogs

            handle = neatlogs.get_prompt(PREFIX + name, label=label)
            if handle.content:
                text, version = handle.content, f"neatlogs-v{handle.version}"
        except Exception as exc:  # not pushed yet, offline, …: the local default is fine
            log.debug("prompt %s: using local default (%s)", name, type(exc).__name__)
    _cache[name] = (text, version)
    return _cache[name]


def push(label: str = "production") -> list[str]:
    """Save every local prompt as a new neatlogs version (only when the text changed)."""
    import neatlogs

    out = []
    for name, text in registry().items():
        full = PREFIX + name
        try:
            current = neatlogs.get_prompt(full, label=label)
            if current.content == text:
                out.append(f"{full}: unchanged (v{current.version})")
                continue
            res = neatlogs.save_as_version(prompt_name=full, content=text, labels=[label],
                                           commit_message=f"MergeMind {local_version(text)}")
            out.append(f"{full}: new version {res.get('version', '?')} labelled {label}")
        except Exception:
            h = neatlogs.create_prompt(name=full, prompt=text, labels=[label],
                                       commit_message=f"MergeMind {local_version(text)}")
            out.append(f"{full}: created v{h.version} labelled {label}")
    return out
