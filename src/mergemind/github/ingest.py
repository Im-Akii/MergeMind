"""Poll GitHub and turn changes into append-only events."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from mergemind.github.client import GitHub, GitHubError
from mergemind.github.commands import parse_commands
from mergemind.models import Event
from mergemind.store.db import Store

log = logging.getLogger(__name__)

WRITE_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def is_bot(user: dict[str, Any] | None) -> bool:
    return bool(user) and (user.get("type") == "Bot" or user.get("login", "").endswith("[bot]"))


def can_write(gh: GitHub, repo: str, comment: dict[str, Any]) -> bool:
    # COLLABORATOR may be a contributor we invited with push access, so confirm the
    # actual permission level before honouring maintainer commands.
    if comment.get("author_association") not in WRITE_ASSOCIATIONS:
        return False
    try:
        return gh.permission(repo, comment["user"]["login"]) in ("admin", "maintain", "write") and (
            comment["user"]["login"] not in _invited_contributors(gh, repo)
        )
    except GitHubError:
        return False


def _invited_contributors(gh: GitHub, repo: str) -> set[str]:
    store = gh.store
    if store is None:
        return set()
    rows = store.conn.execute(
        "SELECT login FROM access_leases l JOIN repos r ON r.id=l.repo_id"
        " WHERE r.full_name=? AND l.status IN ('invited','active')",
        (repo,),
    ).fetchall()
    return {r["login"] for r in rows}


def issue_events(issue: dict[str, Any], repo: str, known: bool) -> list[Event]:
    n = issue["number"]
    author = issue["user"]["login"]
    out: list[Event] = []
    digest = hashlib.sha256(((issue.get("title") or "") + (issue.get("body") or "")).encode()).hexdigest()[:16]
    body_id = f"{repo}#{n}:body:{digest}"
    if not known:
        out.append(Event(repo=repo, number=n, source="poll", kind="issue_opened",
                         external_id=f"{repo}#{n}:opened",
                         payload={"title": issue["title"], "author": author}))
        # record the initial body so only real edits produce issue_edited later
        out.append(Event(repo=repo, number=n, source="poll", kind="body_seen", external_id=body_id))
    else:
        out.append(Event(repo=repo, number=n, source="poll", kind="issue_edited", external_id=body_id))
    if issue.get("state") == "closed":
        out.append(Event(repo=repo, number=n, source="poll", kind="issue_closed",
                         external_id=f"{repo}#{n}:closed:{issue.get('closed_at')}", payload={}))
    elif known and issue.get("locked"):
        out.append(Event(repo=repo, number=n, source="poll", kind="issue_locked",
                         external_id=f"{repo}#{n}:locked", payload={}))
    return out


def comment_events(gh: GitHub, repo: str, number: int, comment: dict[str, Any]) -> list[Event]:
    if is_bot(comment.get("user")):
        return []
    author = comment["user"]["login"]
    base = {"author": author, "comment_id": comment["id"]}
    cmds = parse_commands(comment.get("body") or "")
    if not cmds:
        return [Event(repo=repo, number=number, source="poll", kind="comment",
                      external_id=f"comment:{comment['id']}", payload=base)]
    writer = can_write(gh, repo, comment)
    return [
        Event(repo=repo, number=number, source="command", kind="command",
              external_id=f"comment:{comment['id']}:{i}",
              payload={**base, "name": c.name, "args": c.args, "can_write": writer})
        for i, c in enumerate(cmds)
    ]


def ingest(gh: GitHub, store: Store, repo: str) -> int:
    """Poll one repo. Returns the number of new events."""
    cursor = store.repo_get(repo, "issues_cursor")
    try:
        items = gh.issues_since(repo, cursor)
    except GitHubError as e:
        log.warning("poll %s failed: %s", repo, e)
        return 0
    added = 0
    newest = cursor
    for issue in items:
        if "pull_request" in issue or is_bot(issue.get("user")):
            continue
        n = issue["number"]
        known = store.conn.execute(
            "SELECT 1 FROM events WHERE external_id=?", (f"{repo}#{n}:opened",)
        ).fetchone() is not None
        if not known and issue.get("state") == "closed":
            continue  # never seen open; nothing to do
        for evt in issue_events(issue, repo, known):
            added += store.add_event(evt)
        if issue.get("comments"):
            for c in gh.list_issue_comments(repo, n):
                for evt in comment_events(gh, repo, n, c):
                    added += store.add_event(evt)
        newest = max(newest or "", issue["updated_at"])
    if newest and newest != cursor:
        store.repo_set(repo, issues_cursor=newest)
    return added


def poll_invitations(gh: GitHub, store: Store) -> int:
    """Turn invitation outcomes into events: accepted -> access_accepted, vanished -> invitation_gone."""
    from mergemind.github.access import Leases, is_collaborator

    added = 0
    pending = Leases(store).pending_invites()
    open_invites: dict[str, set[str]] = {}
    for lease in pending:
        repo, login, n = lease["repo"], lease["login"], lease["issue_number"]
        try:
            if is_collaborator(gh, repo, login):
                Leases(store).set_status(lease["id"], "active")
                added += store.add_event(Event(repo=repo, number=n, source="poll", kind="access_accepted",
                                               external_id=f"lease:{lease['id']}:accepted",
                                               payload={"lease_id": lease["id"]}))
                continue
            if repo not in open_invites:
                open_invites[repo] = {(i.get("invitee") or {}).get("login", "").lower()
                                      for i in gh.paginate(f"/repos/{repo}/invitations")}
            if login.lower() not in open_invites[repo]:
                added += store.add_event(Event(repo=repo, number=n, source="poll", kind="invitation_gone",
                                               external_id=f"lease:{lease['id']}:gone",
                                               payload={"lease_id": lease["id"]}))
        except GitHubError as e:
            log.warning("invitation poll failed for %s/%s: %s", repo, login, e)
    return added


def _link(store: Store, repo: str, pr: int, issue: int | None = None) -> int | None:
    rid = store.repo_id(repo)
    if issue is not None:
        store.conn.execute("INSERT OR REPLACE INTO pr_links(repo_id, pr_number, issue_number) VALUES (?,?,?)",
                           (rid, pr, issue))
        return issue
    row = store.conn.execute("SELECT issue_number FROM pr_links WHERE repo_id=? AND pr_number=?", (rid, pr)).fetchone()
    return row["issue_number"] if row else None


def ingest_pulls(gh: GitHub, store: Store, repo: str) -> int:
    """PR lifecycle + CI completion -> events on the *linked issue*."""
    from mergemind.pr.checks import check_runs, summarize
    from mergemind.pr.diff import linked_issue

    cursor = store.repo_get(repo, "pulls_cursor")
    try:
        prs = gh.request("GET", f"/repos/{repo}/pulls", point="gh.list_pulls", use_etag=True,
                         params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 50}) or []
    except GitHubError as e:
        log.warning("poll pulls %s failed: %s", repo, e)
        return 0
    added = 0
    newest = cursor
    for pr in prs:
        n = pr["number"]
        issue = _link(store, repo, n) or linked_issue(pr)
        if issue is None:
            continue
        _link(store, repo, n, issue)
        head = pr["head"]["sha"]
        base = {"pr_number": n, "head_sha": head, "author": pr["user"]["login"]}
        if pr["state"] == "open":
            added += store.add_event(Event(repo=repo, number=issue, source="poll", kind="pr_opened",
                                           external_id=f"{repo}!{n}:opened", payload=base))
            added += store.add_event(Event(repo=repo, number=issue, source="poll", kind="pr_updated",
                                           external_id=f"{repo}!{n}:head:{head}", payload=base))
            try:  # CI finished for this head?
                ci = summarize(check_runs(gh, repo, head))
                if ci.status == "complete":
                    added += store.add_event(Event(repo=repo, number=issue, source="poll", kind="pr_checks_completed",
                                                   external_id=f"{repo}!{n}:checks:{head}:{ci.tests}:{ci.regression}",
                                                   payload=base))
            except GitHubError as e:
                log.warning("check runs for %s!%d failed: %s", repo, n, e)
        elif pr.get("merged_at"):
            added += store.add_event(Event(repo=repo, number=issue, source="poll", kind="pr_merged",
                                           external_id=f"{repo}!{n}:merged",
                                           payload={**base, "merge_sha": pr.get("merge_commit_sha")}))
        else:
            added += store.add_event(Event(repo=repo, number=issue, source="poll", kind="pr_closed_unmerged",
                                           external_id=f"{repo}!{n}:closed:{pr.get('closed_at')}", payload=base))
        # commands typed on the PR thread act on the linked issue
        if pr.get("updated_at", "") > (cursor or ""):
            for c in gh.list_issue_comments(repo, n):
                for evt in comment_events(gh, repo, issue, c):
                    if evt.kind == "command":
                        evt.payload["pr_number"] = n
                        added += store.add_event(evt)
        newest = max(newest or "", pr.get("updated_at") or "")
    if newest and newest != cursor:
        store.repo_set(repo, pulls_cursor=newest)
    return added
