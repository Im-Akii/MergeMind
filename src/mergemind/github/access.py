"""Contributor access: branch-rule preflight, collaborator invites, acceptance, revocation,
and the access-lease records (buildplan-v2 §7.4)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mergemind.github.client import GitHub, GitHubError
from mergemind.store.db import Store
from mergemind.tracing import span


@dataclass
class RulesCheck:
    ok: bool
    branch: str
    missing: list[str] = field(default_factory=list)
    found: list[str] = field(default_factory=list)


@span("TOOL", "access.check_rules")
def check_branch_rules(gh: GitHub, repo: str, branch: str, required_checks: list[str] | None = None) -> RulesCheck:
    """The default branch must require a PR with ≥1 approval and block force-pushes and
    deletion before any outsider gets push access. Reads both rulesets and classic protection."""
    found: set[str] = set()
    approvals = 0
    checks: set[str] = set()
    try:
        for rule in gh.request("GET", f"/repos/{repo}/rules/branches/{branch}", point="gh.rules") or []:
            t = rule.get("type")
            found.add(t)
            params = rule.get("parameters") or {}
            if t == "pull_request":
                approvals = max(approvals, int(params.get("required_approving_review_count") or 0))
            if t == "required_status_checks":
                checks |= {c.get("context") for c in params.get("required_status_checks", [])}
    except GitHubError as e:
        if e.error_class != "not_found":
            raise
    try:  # classic branch protection (needs admin to read; absence is fine)
        prot = gh.request("GET", f"/repos/{repo}/branches/{branch}/protection", point="gh.protection")
        if prot.get("required_pull_request_reviews"):
            found.add("pull_request")
            approvals = max(approvals, int(prot["required_pull_request_reviews"].get("required_approving_review_count") or 0))
        if not (prot.get("allow_force_pushes") or {}).get("enabled", False):
            found.add("non_fast_forward")
        if not (prot.get("allow_deletions") or {}).get("enabled", False):
            found.add("deletion")
        checks |= set((prot.get("required_status_checks") or {}).get("contexts") or [])
    except GitHubError as e:
        if e.error_class not in ("not_found", "permission"):
            raise

    missing = []
    if "pull_request" not in found or approvals < 1:
        missing.append("require a pull request with at least 1 approving review")
    if "non_fast_forward" not in found:
        missing.append("block force pushes")
    if "deletion" not in found:
        missing.append("block branch deletion")
    for c in required_checks or []:
        if c not in checks:
            missing.append(f"require the status check `{c}`")
    return RulesCheck(ok=not missing, branch=branch, missing=missing, found=sorted(found))


def account_age_days(gh: GitHub, login: str) -> float:
    user = gh.request("GET", f"/users/{login}", point="gh.user")
    created = datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 86400


def current_permission(gh: GitHub, repo: str, login: str) -> str:
    """admin | maintain | write | triage | read | none"""
    try:
        data = gh.request("GET", f"/repos/{repo}/collaborators/{login}/permission", point="gh.permission")
    except GitHubError as e:
        if e.error_class == "not_found":
            return "none"
        raise
    return data.get("role_name") or data.get("permission") or "none"


@span("TOOL", "access.invite")
def invite(gh: GitHub, repo: str, login: str, permission: str = "push") -> str:
    """Returns 'invited' (invitation created) or 'already' (already a collaborator)."""
    res = gh.request("PUT", f"/repos/{repo}/collaborators/{login}", point="gh.invite",
                     json_body={"permission": permission})
    return "invited" if res else "already"


def is_collaborator(gh: GitHub, repo: str, login: str) -> bool:
    try:
        gh.request("GET", f"/repos/{repo}/collaborators/{login}", point="gh.is_collaborator")
        return True
    except GitHubError as e:
        if e.error_class == "not_found":
            return False
        raise


@span("TOOL", "access.revoke")
def revoke(gh: GitHub, repo: str, login: str) -> None:
    """Cancel a pending invitation and remove collaborator access. Both are idempotent."""
    for inv in gh.paginate(f"/repos/{repo}/invitations"):
        if (inv.get("invitee") or {}).get("login", "").lower() == login.lower():
            gh.request("DELETE", f"/repos/{repo}/invitations/{inv['id']}", point="gh.revoke_invite")
    try:
        gh.request("DELETE", f"/repos/{repo}/collaborators/{login}", point="gh.revoke")
    except GitHubError as e:
        if e.error_class != "not_found":
            raise


# ----------------------------------------------------------------------- leases


class Leases:
    def __init__(self, store: Store):
        self.store = store

    def create(self, repo: str, number: int, login: str, mode: str, status: str, ttl_s: float) -> int:
        now = time.time()
        cur = self.store.conn.execute(
            "INSERT INTO access_leases(repo_id, issue_number, login, mode, status, invited_at, accepted_at,"
            " expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.store.repo_id(repo), number, login, mode, status, now,
             now if status == "active" else None, now + ttl_s))
        return int(cur.lastrowid)

    def current(self, repo: str, number: int) -> dict[str, Any] | None:
        row = self.store.conn.execute(
            "SELECT * FROM access_leases WHERE repo_id=? AND issue_number=? AND status IN ('invited','active')"
            " ORDER BY id DESC LIMIT 1", (self.store.repo_id(repo), number)).fetchone()
        return dict(row) if row else None

    def active_for(self, repo: str, login: str) -> list[dict[str, Any]]:
        rows = self.store.conn.execute(
            "SELECT * FROM access_leases WHERE repo_id=? AND lower(login)=lower(?) AND status IN ('invited','active')",
            (self.store.repo_id(repo), login)).fetchall()
        return [dict(r) for r in rows]

    def pending_invites(self) -> list[dict[str, Any]]:
        rows = self.store.conn.execute(
            "SELECT l.*, r.full_name AS repo FROM access_leases l JOIN repos r ON r.id=l.repo_id"
            " WHERE l.status='invited'").fetchall()
        return [dict(r) for r in rows]

    def set_status(self, lease_id: int, status: str) -> None:
        col = {"active": "accepted_at", "revoked": "revoked_at", "expired": "revoked_at", "merged": "revoked_at"}.get(status)
        sql = "UPDATE access_leases SET status=?" + (f", {col}=?" if col else "") + " WHERE id=?"
        self.store.conn.execute(sql, (status, time.time(), lease_id) if col else (status, lease_id))
