"""Parse slash commands from comment bodies. Authorization happens in the ingestor."""

from __future__ import annotations

import re
from dataclasses import dataclass

# only lines that *start* with a command count; quoted lines ("> /contribute") don't
_LINE = re.compile(r"^\s*/(contribute|mergemind)\b[ \t]*(.*)$", re.M)

MERGEMIND_SUBCOMMANDS = {
    "withdraw",
    "recheck",
    "approve-access",
    "approve",
    "request-changes",
    "accept",
    "not-duplicate",
    "revoke",
    "stop",
    "autonomy",
    "why",
}


@dataclass(frozen=True)
class Command:
    name: str
    args: str = ""


def _strip_code(body: str) -> str:
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    body = re.sub(r"`[^`\n]*`", "", body)
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))


def parse_commands(body: str) -> list[Command]:
    out: list[Command] = []
    for m in _LINE.finditer(_strip_code(body or "")):
        head, rest = m.group(1), m.group(2).strip()
        if head == "contribute":
            out.append(Command("contribute"))
            continue
        sub, _, args = rest.partition(" ")
        if sub in MERGEMIND_SUBCOMMANDS:
            out.append(Command(sub, args.strip()))
    return out
