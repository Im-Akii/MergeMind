"""Cleaning untrusted text (issue/PR bodies, comments) before a model sees it."""

from __future__ import annotations

import re

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# zero-width, bidi controls, BOM
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")

BODY_CAP = 8000
COMMENT_CAP = 2000
MAX_COMMENTS = 20


def clean(text: str | None, cap: int = BODY_CAP) -> str:
    text = _HTML_COMMENT.sub("", text or "")
    text = _INVISIBLE.sub("", text)
    if len(text) > cap:
        text = text[:cap] + "\n…[truncated]"
    return text.strip()


def wrap_untrusted(tag: str, text: str) -> str:
    # neutralize attempts to close our wrapper tag early
    text = text.replace(f"</{tag}>", f"</ {tag}>")
    return f"<{tag}>\n{text}\n</{tag}>"


def issue_for_model(issue: dict, comments: list[dict]) -> str:
    parts = [f"Title: {clean(issue.get('title'), 300)}", f"Author: {issue['user']['login']}", "", clean(issue.get("body"))]
    for c in comments[-MAX_COMMENTS:]:
        parts.append(f"\n--- comment by {c['user']['login']} ---\n{clean(c.get('body'), COMMENT_CAP)}")
    return wrap_untrusted("untrusted_issue", "\n".join(parts))
