"""Publish a generated project to a new GitHub repository and open a pull request.

The flow uses only documented REST endpoints and the smallest permission set that covers them
(fine-grained token: Administration and Repository creation write to create the repository,
Contents write to commit, Workflows write because every generated project contains
``.github/workflows/ci.yml``, Pull requests write to open the PR):

1. ``POST /user/repos`` (or ``/orgs/{owner}/repos``) with ``auto_init`` so a default branch
   exists. An existing name is refused, never reused: this code never writes to a repository it
   did not just create.
2. Read the default branch's commit and tree (retrying briefly while GitHub initialises it).
3. ``POST git/trees`` with every file's content inline (GitHub creates the blobs), on top of the
   initial tree; ``POST git/commits``; ``POST git/refs`` for a new ``autoswe/<job>`` branch.
4. ``POST pulls`` from that branch into the default branch, so the generated code lands as a
   reviewable PR rather than straight on the default branch.

The token is sent only in the ``Authorization`` header, never logged, never stored, and scrubbed
from any error text that is returned or logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx2

from autoswe import __version__
from autoswe.pipeline.artifacts import GeneratedFile

logger = logging.getLogger(__name__)
_sleep = asyncio.sleep  # indirection so tests can skip backoff waits

Visibility = Literal["private", "public"]
API_VERSION = "2022-11-28"
PROJECT_URL = "https://github.com/Arya-9989/Autonomous-AI-Software-Engineering-Platform"
# GitHub repository names: letters, digits, '.', '-', '_'; at most 100 characters.
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
OWNER_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def repository_name_problem(name: str) -> str | None:
    """Why ``name`` cannot be used for a new repository, or ``None`` if it can."""
    if not REPOSITORY_NAME.match(name):
        return "use 1-100 letters, digits, '.', '-' or '_'"
    if name in {".", ".."} or name.startswith("."):
        return "must not start with '.'"
    if name.lower().endswith(".git"):
        return "must not end with '.git'"
    return None


class PublishError(Exception):
    status_code = 502
    code = "github_error"
    retry_after: str | None = None


class PublishNotConfiguredError(PublishError):
    status_code = 503
    code = "publishing_not_configured"


class RepositoryExistsError(PublishError):
    status_code = 409
    code = "repository_exists"


class GitHubAuthError(PublishError):
    code = "github_auth_failed"


class GitHubRateLimitedError(PublishError):
    status_code = 503
    code = "github_rate_limited"


class GitHubRequestError(PublishError):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Publication:
    repository: str
    html_url: str
    pull_request_url: str
    pull_request_number: int
    branch: str
    base_branch: str
    commit_sha: str
    visibility: Visibility
    published_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"status": "published", **asdict(self)}


class GitHubClient:
    """A small, careful GitHub REST client: timeouts, bounded retries for safe requests only,
    typed errors, and a token that never leaves the Authorization header."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        timeout_s: float = 30.0,
        retries: int = 2,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._retries = retries
        self._client = httpx2.AsyncClient(
            base_url=api_url.rstrip("/"),
            timeout=httpx2.Timeout(timeout_s, connect=10.0),
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": f"autoswe/{__version__}",
            },
        )

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    def scrub(self, text: str) -> str:
        return text.replace(self._token, "[redacted]") if self._token else text

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        safe_to_retry: bool = False,
    ) -> dict[str, Any]:
        """One API call. Only ``safe_to_retry`` calls (reads, or writes that cannot duplicate)
        are retried, on connection errors, timeouts and 5xx."""
        for attempt in range(self._retries + 1):
            last = attempt == self._retries
            try:
                response = await self._client.request(method, path, json=body)
            except httpx2.TimeoutException as exc:
                if safe_to_retry and not last:
                    await _sleep(2**attempt)
                    continue
                raise PublishError("GitHub did not answer in time") from exc
            except httpx2.TransportError as exc:
                if safe_to_retry and not last:
                    await _sleep(2**attempt)
                    continue
                raise PublishError("cannot reach GitHub") from exc
            status = response.status_code
            if 200 <= status < 300:
                payload = response.json() if response.content else {}
                return payload if isinstance(payload, dict) else {"items": payload}
            if safe_to_retry and status >= 500 and not last:
                await _sleep(2**attempt)
                continue
            raise self._error(response)
        raise PublishError("unreachable")  # pragma: no cover

    def _error(self, response: httpx2.Response) -> PublishError:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message = self.scrub(str(payload.get("message") or response.reason_phrase))[:200]
        details = " ".join(
            self.scrub(str(error.get("message") or error.get("code") or ""))[:200]
            for error in payload.get("errors") or []
            if isinstance(error, dict)
        )
        status = response.status_code
        if status == 401:
            return GitHubAuthError(f"GitHub rejected the publishing token ({message})")
        remaining = response.headers.get("x-ratelimit-remaining")
        retry_after = response.headers.get("retry-after")
        if status == 429 or (status == 403 and (remaining == "0" or retry_after)):
            error = GitHubRateLimitedError(f"GitHub rate limit reached ({message})")
            error.retry_after = retry_after or "60"
            return error
        if status == 422 and "already exists" in f"{message} {details}":
            return RepositoryExistsError("a repository with that name already exists")
        text = f"GitHub API error {status}: {message}" + (f" ({details})" if details else "")
        return GitHubRequestError(text, status)


def _clean(text: str, limit: int) -> str:
    return _CONTROL.sub(" ", text).strip()[:limit]


async def publish_project(
    client: GitHubClient,
    *,
    owner: str | None,
    name: str,
    visibility: Visibility,
    description: str,
    files: list[GeneratedFile],
    branch: str,
    title: str,
    body: str,
    ref_attempts: int = 6,
) -> Publication:
    """Create the repository, commit ``files`` on ``branch`` and open the pull request."""
    problem = repository_name_problem(name)
    if problem:
        raise GitHubRequestError(f"invalid repository name: {problem}", 422)
    repo_body: dict[str, Any] = {
        "name": name,
        "description": _clean(description, 350),
        "private": visibility == "private",
        "auto_init": True,
    }
    if owner:
        repo_body["visibility"] = visibility
    # Not retried: a retry after a lost response could hit "already exists" for our own repo.
    repo = await client.request("POST", f"/orgs/{owner}/repos" if owner else "/user/repos",
                                repo_body)  # fmt: skip
    full_name = str(repo["full_name"])
    html_url = str(repo["html_url"])
    base = str(repo.get("default_branch") or "main")
    try:
        # auto_init creates the first commit asynchronously: the ref can 404/409 for a moment.
        ref = await _with_backoff(
            lambda: client.request(
                "GET", f"/repos/{full_name}/git/ref/heads/{base}", safe_to_retry=True
            ),
            ref_attempts,
        )
        parent = str(ref["object"]["sha"])
        commit = await client.request(
            "GET", f"/repos/{full_name}/git/commits/{parent}", safe_to_retry=True
        )
        tree = await client.request(
            "POST",
            f"/repos/{full_name}/git/trees",
            {
                "base_tree": commit["tree"]["sha"],
                "tree": [
                    {"path": f.path, "mode": "100644", "type": "blob", "content": f.content}
                    for f in sorted(files, key=lambda f: f.path)
                ],
            },
            safe_to_retry=True,  # a tree is content-addressed: creating it twice is harmless
        )
        new_commit = await client.request(
            "POST",
            f"/repos/{full_name}/git/commits",
            {"message": _clean(title, 200) + "\n\nGenerated by AutoSWE.", "tree": tree["sha"],
             "parents": [parent]},
        )  # fmt: skip
        await client.request(
            "POST",
            f"/repos/{full_name}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": new_commit["sha"]},
        )
        pull = await client.request(
            "POST",
            f"/repos/{full_name}/pulls",
            {"title": _clean(title, 200), "head": branch, "base": base, "body": body[:60_000]},
        )
    except PublishError as exc:
        # The repository exists now; say where, so the user can finish or delete it.
        exc.args = (f"{exc.args[0] if exc.args else exc}; the repository {html_url} was created "
                    "but the pull request was not opened",)  # fmt: skip
        raise
    return Publication(
        repository=full_name,
        html_url=html_url,
        pull_request_url=str(pull["html_url"]),
        pull_request_number=int(pull["number"]),
        branch=branch,
        base_branch=base,
        commit_sha=str(new_commit["sha"]),
        visibility=visibility,
        published_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )


async def _with_backoff(call: Any, attempts: int) -> dict[str, Any]:
    for attempt in range(attempts):
        try:
            result: dict[str, Any] = await call()
            return result
        except GitHubRequestError as exc:
            if exc.status not in {404, 409} or attempt == attempts - 1:
                raise
            await _sleep(min(2**attempt * 0.5, 4))
    raise PublishError("unreachable")  # pragma: no cover


def pull_request_body(job: Any, files: int) -> str:
    """What the PR says: the spec, the platform's verdicts and a reminder to review."""
    review = (job.review or {}).get("score")
    validation = job.validation or {}
    errors = sum(1 for i in validation.get("issues", []) if i.get("severity") == "error")
    tests = ((job.test_report or {}).get("final") or {}).get("tests") or {}
    test_line = (
        f"- Tests in the sandbox: {tests.get('passed', 0)}/{tests.get('collected', 0)} passed"
        if job.test_report
        else "- Tests: not executed (no sandbox configured)"
    )
    spec = _CONTROL.sub(" ", job.spec)[:4_000]
    return "\n".join([
        f"Generated by [AutoSWE]({PROJECT_URL}) from this specification (job `{job.id}`):",
        "", *(f"> {line}" for line in spec.splitlines() or [""]), "",
        f"- Files: {files}",
        f"- Structural validation: {'passed' if errors == 0 else f'{errors} error(s)'}",
        test_line,
        f"- Review score: {review}/100" if review is not None else "- Review score: n/a",
        "",
        "Review the code before merging; the platform's checks do not judge business logic.",
    ])  # fmt: skip
