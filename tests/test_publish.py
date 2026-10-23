"""Publishing a generated project to a new GitHub repository with a pull request.

Everything runs against ``tests/fake_github.py``; no request reaches github.com.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import httpx2
import pytest

from autoswe.config import Settings
from autoswe.publishing import (
    GitHubAuthError,
    GitHubClient,
    GitHubRateLimitedError,
    PublishError,
    RepositoryExistsError,
    publish_project,
    repository_name_problem,
)
from tests.conftest import ALICE, BOB, AppHarness, HarnessFactory
from tests.fake_github import PERMISSIONS, FakeGitHub, create_fake_github
from tests.sandbox_projects import passing

TOKEN = "github_pat_fake_0123456789abcdef"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr("autoswe.publishing._sleep", instant)


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub(token=TOKEN)


async def _published_job(
    harness_factory: HarnessFactory, github: FakeGitHub, **settings: Any
) -> tuple[AppHarness, str]:
    harness = await harness_factory(github_publish_token=TOKEN, **settings)
    harness.app.state.services.github_transport = httpx2.ASGITransport(
        app=create_fake_github(github)
    )
    job = await harness.submit()
    await harness.wait_for(job["id"], {"succeeded"})
    return harness, job["id"]


async def _publish(harness: AppHarness, job_id: str, headers: dict[str, str] = ALICE,
                   **body: Any) -> httpx.Response:  # fmt: skip
    payload = {"repository": "library-api", **body}
    return await harness.client.post(f"/api/v1/jobs/{job_id}/publish", json=payload,
                                     headers=headers)  # fmt: skip


async def test_a_job_is_published_as_a_new_repository_with_a_pull_request(
    harness_factory: HarnessFactory, github: FakeGitHub, caplog: pytest.LogCaptureFixture
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    with caplog.at_level(logging.DEBUG):
        response = await _publish(harness, job_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "published"
    assert body["repository"] == "octo-demo/library-api"
    assert body["visibility"] == "private"  # private unless asked otherwise
    assert body["pull_request_url"] == "https://github.example/octo-demo/library-api/pull/1"
    assert body["branch"] == f"autoswe/{job_id[:8]}" and body["base_branch"] == "main"

    repo = github.repos["octo-demo/library-api"]
    assert repo.private is True
    files = github.files_at("octo-demo/library-api", body["branch"])
    assert "app/main.py" in files and ".github/workflows/ci.yml" in files
    assert files["README.md"] != "# library-api\n"  # the project's README replaces auto_init's
    (pull,) = repo.pulls
    assert pull["head"] == body["branch"] and pull["base"] == "main"
    assert "Review score: 100/100" in pull["body"]
    assert "Structural validation: passed" in pull["body"]
    # The default branch is untouched: the code arrives as a reviewable PR.
    assert github.files_at("octo-demo/library-api", "main") == {"README.md": "# library-api\n"}

    detail = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
    assert detail["publication"]["pull_request_url"] == body["pull_request_url"]
    events = (await harness.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)).text
    assert "job.published" in events
    # The token went only into the Authorization header.
    assert all(r["headers"]["authorization"] == f"Bearer {TOKEN}" for r in github.requests)
    assert TOKEN not in caplog.text and TOKEN not in response.text and TOKEN not in events
    assert (
        'autoswe_github_publish_total{outcome="published"}'
        in (await harness.client.get("/metrics")).text
    )


async def test_organisation_repositories_use_the_org_endpoint(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness, job_id = await _published_job(harness_factory, github,
                                           github_publish_owner="acme-labs")  # fmt: skip
    response = await _publish(harness, job_id, visibility="public")
    assert response.status_code == 201, response.text
    assert response.json()["repository"] == "acme-labs/library-api"
    assert github.repos["acme-labs/library-api"].private is False
    assert github.requests[0]["path"] == "/orgs/acme-labs/repos"


async def test_publishing_needs_a_configured_token(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory()
    job = await harness.submit()
    await harness.wait_for(job["id"], {"succeeded"})
    response = await _publish(harness, job["id"])
    assert response.status_code == 503
    assert "GITHUB_PUBLISH_TOKEN" in response.json()["detail"]


@pytest.mark.parametrize(
    "name", ["", "a/b", "../x", ".hidden", "repo.git", "with space", "x" * 101, "naïve"]
)
async def test_repository_names_are_validated(
    harness_factory: HarnessFactory, github: FakeGitHub, name: str
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    response = await _publish(harness, job_id, repository=name)
    assert response.status_code == 422
    assert github.requests == []


async def test_callers_cannot_choose_the_owner_or_inject_fields(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    extras: list[dict[str, Any]] = [{"owner": "someone-else"}, {"token": "x"},
                                    {"visibility": "internal"}]  # fmt: skip
    for extra in extras:
        assert (await _publish(harness, job_id, **extra)).status_code == 422
    assert github.requests == []


async def test_only_the_owner_can_publish_a_succeeded_job_once(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    assert (await _publish(harness, job_id, headers=BOB)).status_code == 404
    assert (await _publish(harness, job_id)).status_code == 201
    again = await _publish(harness, job_id, repository="another-name")
    assert again.status_code == 409
    assert "already published" in again.json()["detail"]
    assert len(github.repos) == 1


async def test_concurrent_publishes_create_one_repository(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    results = await asyncio.gather(*(
        _publish(harness, job_id, repository=f"race-{i}") for i in range(5)
    ))  # fmt: skip
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409, 409]
    assert len(github.repos) == 1


async def test_a_taken_name_is_refused_and_can_be_retried(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    github.repos["octo-demo/library-api"] = object()  # type: ignore[assignment]  # taken
    taken = await _publish(harness, job_id)
    assert taken.status_code == 409
    assert taken.json()["detail"] == "a repository with that name already exists"
    # Nothing was created, so the job can be published under another name.
    assert (await _publish(harness, job_id, repository="library-api-2")).status_code == 201


async def test_unfinished_jobs_are_not_published(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness = await harness_factory(github_publish_token=TOKEN, worker_enabled=False)
    job = await harness.submit()
    response = await _publish(harness, job["id"])
    assert response.status_code == 409


async def test_github_errors_map_to_clear_api_errors(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    github.token = "rotated-token"  # the server's token is now wrong
    denied = await _publish(harness, job_id)
    assert denied.status_code == 502
    assert denied.json()["detail"].startswith("GitHub rejected the publishing token")
    github.token = TOKEN
    github.failures["create_repo"] = [403]
    limited = await _publish(harness, job_id)
    assert limited.status_code == 503 and limited.headers["retry-after"] == "30"
    # Both failed before anything was created: a later attempt still works.
    assert (await _publish(harness, job_id)).status_code == 201


async def test_a_failure_after_the_repository_exists_says_where_it_is(
    harness_factory: HarnessFactory, github: FakeGitHub
) -> None:
    harness, job_id = await _published_job(harness_factory, github)
    github.failures["pulls"] = [500]  # not retried: opening a PR twice would duplicate it
    response = await _publish(harness, job_id)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "https://github.example/octo-demo/library-api was created" in detail
    job = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
    assert job["publication"]["status"] == "failed"
    assert (await _publish(harness, job_id)).status_code == 409  # never written to twice


async def test_a_workflow_needs_the_workflows_permission(
    harness_factory: HarnessFactory,
) -> None:
    github = FakeGitHub(token=TOKEN, permissions=PERMISSIONS - {"workflows"})
    harness, job_id = await _published_job(harness_factory, github)
    response = await _publish(harness, job_id)
    assert response.status_code == 502
    assert "workflows" in response.json()["detail"]


# --- the client on its own ----------------------------------------------------------------------


def _client(github: FakeGitHub, token: str = TOKEN) -> GitHubClient:
    return GitHubClient(token, api_url="http://fake",
                        transport=httpx2.ASGITransport(app=create_fake_github(github)))  # fmt: skip


async def _publish_files(client: GitHubClient, **kwargs: Any) -> Any:
    return await publish_project(
        client, owner=None, name=kwargs.pop("name", "proj"), visibility="private",
        description="d", files=passing(), branch="autoswe/x", title="t", body="b", **kwargs,
    )  # fmt: skip


async def test_the_publisher_waits_for_auto_init_and_retries_safe_reads(
    github: FakeGitHub,
) -> None:
    github.init_delay_reads = 3  # the ref is not there for the first reads
    github.failures["commit"] = [502]  # a transient 5xx on a read is retried
    async with _client(github) as client:
        publication = await _publish_files(client)
    assert publication.commit_sha
    assert sum(r["endpoint"] == "ref" for r in github.requests) == 4
    assert sum(r["endpoint"] == "commit" for r in github.requests) == 2


async def test_the_initial_commit_never_appearing_is_an_error(github: FakeGitHub) -> None:
    github.init_delay_reads = 100
    async with _client(github) as client:
        with pytest.raises(PublishError, match="was created"):
            await _publish_files(client, ref_attempts=3)


async def test_client_error_types_and_token_scrubbing(github: FakeGitHub) -> None:
    async with _client(github, token="wrong-token-0123456789") as client:
        with pytest.raises(GitHubAuthError):
            await client.request("GET", "/user")
    github.repos["octo-demo/proj"] = object()  # type: ignore[assignment]
    async with _client(github) as client:
        with pytest.raises(RepositoryExistsError):
            await _publish_files(client)
        github.failures["user"] = [429]
        with pytest.raises(GitHubRateLimitedError):
            await client.request("GET", "/user")
        assert client.scrub(f"echo {TOKEN}") == "echo [redacted]"

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    async with GitHubClient(TOKEN, transport=httpx2.MockTransport(unreachable)) as client:
        with pytest.raises(PublishError, match="cannot reach GitHub"):
            await client.request("GET", "/user", safe_to_retry=True)
        # A repository creation is never retried blindly.
        with pytest.raises(PublishError, match="cannot reach GitHub"):
            await client.request("POST", "/user/repos", {"name": "x"})


@pytest.mark.parametrize(
    ("name", "ok"),
    [("library-api", True), ("A.b_c-1", True), (".x", False), ("x.git", False), ("..", False)],
)
def test_repository_name_rules(name: str, ok: bool) -> None:
    assert (repository_name_problem(name) is None) is ok


@pytest.mark.parametrize(
    ("url", "insecure", "valid"),
    [
        ("https://api.github.com", False, True),
        ("https://github.example.com/api/v3", False, True),
        ("http://127.0.0.1:5366", False, True),
        ("http://fake-github:8080", False, False),  # the token would travel in clear
        ("http://fake-github:8080", True, True),
        ("ftp://x", False, False),
        ("not a url", False, False),
    ],
)
def test_github_api_url_must_be_https(url: str, insecure: bool, valid: bool) -> None:
    def build() -> Settings:
        return Settings(_env_file=None, auth_disabled=True, github_api_url=url,
                        github_api_insecure_http=insecure)  # fmt: skip

    if valid:
        assert build().github_api_url == url
    else:
        with pytest.raises(ValueError, match="GITHUB_API_URL"):
            build()


def test_the_token_is_secret_in_settings() -> None:
    settings = Settings(_env_file=None, auth_disabled=True, github_publish_token=TOKEN)
    assert settings.github_publishing_enabled
    assert TOKEN not in repr(settings) and TOKEN not in str(settings.model_dump())
    assert not Settings(_env_file=None, auth_disabled=True,
                        github_publish_token="").github_publishing_enabled  # fmt: skip
