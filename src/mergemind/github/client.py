"""Thin GitHub REST/GraphQL client: ETag caching, rate limits, retries, fault injection."""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

import httpx

from mergemind.faults import NO_FAULTS, Faults, InjectedFault
from mergemind.store.db import Store
from mergemind.tracing import span

log = logging.getLogger(__name__)

API = "https://api.github.com"


class GitHubError(Exception):
    """Classified error. error_class drives recovery (buildplan §13)."""

    def __init__(self, status: int, error_class: str, message: str, reset_at: float | None = None):
        super().__init__(f"{status} {error_class}: {message}")
        self.status = status
        self.error_class = error_class
        self.reset_at = reset_at


def classify(status: int, headers: httpx.Headers, body: str) -> GitHubError:
    msg = body[:300]
    if status == 429 or (
        status == 403
        and (headers.get("x-ratelimit-remaining") == "0" or "rate limit" in body.lower())
    ):
        reset = headers.get("x-ratelimit-reset")
        retry_after = headers.get("retry-after")
        reset_at = (
            float(reset)
            if reset
            else time.time() + float(retry_after or 60)
        )
        return GitHubError(status, "rate_limited", msg, reset_at)
    if status == 403:
        return GitHubError(status, "permission", msg)
    if status == 404:
        return GitHubError(status, "not_found", msg)
    if status in (405, 409):
        return GitHubError(status, "conflict", msg)
    if status == 422:
        return GitHubError(status, "unprocessable", msg)
    if status >= 500:
        return GitHubError(status, "server", msg)
    return GitHubError(status, "client", msg)


class GitHub:
    def __init__(
        self,
        token: str | None,
        store: Store | None = None,
        faults: Faults = NO_FAULTS,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mergemind",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=API, headers=headers, timeout=30, transport=transport)
        self.store = store
        self.faults = faults
        self.max_retries = max_retries
        self.rate_remaining: int | None = None

    # ---------------------------------------------------------------- core

    def request(
        self,
        method: str,
        path: str,
        *,
        point: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        use_etag: bool = False,
    ) -> Any:
        """Returns parsed JSON (or None for 204). Raises GitHubError when classified as final."""
        point = point or f"gh.{method.lower()}"
        cache_key = f"{path}?{json.dumps(params, sort_keys=True)}" if use_etag else None
        attempt = 0
        while True:
            attempt += 1
            fault = self.faults.check(point)
            if fault:
                err = self._fault_error(point, fault)
                if err.error_class == "server" and attempt <= self.max_retries:
                    self._backoff(attempt)
                    continue
                raise err

            headers = {}
            cached_body = None
            if cache_key and self.store:
                etag, cached_body = self.store.gh_cache_get(cache_key)
                if etag:
                    headers["If-None-Match"] = etag

            try:
                resp = self.http.request(
                    method, path, params=params, json=json_body, headers=headers
                )
            except httpx.TransportError as exc:
                if attempt <= self.max_retries:
                    self._backoff(attempt)
                    continue
                raise GitHubError(0, "network", str(exc)) from exc

            rem = resp.headers.get("x-ratelimit-remaining")
            if rem is not None:
                self.rate_remaining = int(rem)

            if resp.status_code == 304 and cached_body is not None:
                return json.loads(cached_body)
            if resp.is_success:
                if resp.status_code == 204 or not resp.content:
                    return None
                data = resp.json()
                if cache_key and self.store and resp.headers.get("etag"):
                    self.store.gh_cache_put(cache_key, resp.headers["etag"], resp.text)
                return data

            err = classify(resp.status_code, resp.headers, resp.text)
            if err.error_class in ("server",) and attempt <= self.max_retries:
                self._backoff(attempt)
                continue
            raise err

    def _fault_error(self, point: str, kind: str) -> GitHubError:
        table = {
            "429": GitHubError(429, "rate_limited", f"injected at {point}", time.time() + 60),
            "403": GitHubError(403, "permission", f"injected at {point}"),
            "404": GitHubError(404, "not_found", f"injected at {point}"),
            "409": GitHubError(409, "conflict", f"injected at {point}"),
            "500": GitHubError(500, "server", f"injected at {point}"),
        }
        if kind not in table:
            raise InjectedFault(point, kind)
        return table[kind]

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(30.0, (2 ** (attempt - 1)) + random.random()))

    def paginate(self, path: str, params: dict[str, Any] | None = None, limit: int = 1000) -> list:
        params = dict(params or {}, per_page=100)
        out: list = []
        page = 1
        while len(out) < limit:
            batch = self.request("GET", path, params={**params, "page": page})
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return out[:limit]

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self.request(
            "POST", "/graphql", point="gh.graphql", json_body={"query": query, "variables": variables or {}}
        )
        if data.get("errors"):
            raise GitHubError(200, "graphql", json.dumps(data["errors"])[:300])
        return data["data"]

    def raw(self, path: str, point: str = "gh.raw", max_bytes: int = 2_000_000) -> str:
        """Plain-text endpoints (e.g. job logs, which redirect to blob storage)."""
        if self.faults.check(point):
            raise GitHubError(500, "server", f"injected at {point}")
        resp = self.http.get(path, follow_redirects=True)
        if not resp.is_success:
            raise classify(resp.status_code, resp.headers, resp.text)
        return resp.text[-max_bytes:]

    # ------------------------------------------------------------- reads

    @span("TOOL", "gh.get_issue")
    def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        return self.request("GET", f"/repos/{repo}/issues/{number}", point="gh.get_issue", use_etag=True)

    def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self.paginate(f"/repos/{repo}/issues/{number}/comments")

    def issues_since(self, repo: str, since: str | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"state": "all", "sort": "updated", "direction": "asc", "per_page": 100}
        if since:
            params["since"] = since
        return self.request(
            "GET", f"/repos/{repo}/issues", point="gh.list_issues", params=params, use_etag=True
        ) or []

    def get_repo(self, repo: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{repo}", point="gh.get_repo", use_etag=True)

    def get_pull(self, repo: str, number: int) -> dict[str, Any]:
        return self.request("GET", f"/repos/{repo}/pulls/{number}", point="gh.get_pull")

    def permission(self, repo: str, login: str) -> str:
        data = self.request(
            "GET", f"/repos/{repo}/collaborators/{login}/permission", point="gh.permission"
        )
        return data.get("permission", "none")

    def get_file(self, repo: str, path: str, ref: str | None = None) -> str | None:
        import base64

        try:
            data = self.request(
                "GET",
                f"/repos/{repo}/contents/{path}",
                point="gh.get_file",
                params={"ref": ref} if ref else None,
            )
        except GitHubError as e:
            if e.error_class == "not_found":
                return None
            raise
        return base64.b64decode(data["content"]).decode("utf-8", "replace")

    # ------------------------------------------------------------ writes

    @span("TOOL", "gh.comment")
    def create_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        return self.request(
            "POST", f"/repos/{repo}/issues/{number}/comments", point="gh.comment", json_body={"body": body}
        )

    def update_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        return self.request(
            "PATCH", f"/repos/{repo}/issues/comments/{comment_id}", point="gh.comment", json_body={"body": body}
        )

    def add_labels(self, repo: str, number: int, labels: list[str]) -> Any:
        return self.request(
            "POST", f"/repos/{repo}/issues/{number}/labels", point="gh.label", json_body={"labels": labels}
        )
