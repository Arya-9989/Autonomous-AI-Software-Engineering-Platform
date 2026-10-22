from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from autoswe.security import ApiKeyAuthenticator, extract_api_key
from tests.conftest import ALICE, BOB, LIBRARY_SPEC, AppHarness, HarnessFactory, make_settings

PROTECTED = [
    ("GET", "/api/v1/jobs"),
    ("POST", "/api/v1/jobs"),
    ("GET", "/api/v1/jobs/some-id"),
    ("GET", "/api/v1/jobs/some-id/events"),
    ("GET", "/api/v1/jobs/some-id/archive"),
    ("POST", "/api/v1/jobs/some-id/cancel"),
    ("DELETE", "/api/v1/jobs/some-id"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-key-but-long-enough"}, {"Authorization": "Basic abc"}],
)
async def test_protected_endpoints_require_a_valid_key(
    harness: AppHarness, method: str, path: str, headers: dict[str, str]
) -> None:
    response = await harness.client.request(method, path, headers=headers, json={"spec": "x" * 30})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_bearer_and_x_api_key_headers_are_both_accepted(harness: AppHarness) -> None:
    assert (await harness.client.get("/api/v1/jobs", headers=ALICE)).status_code == 200
    assert (await harness.client.get("/api/v1/jobs", headers=BOB)).status_code == 200


async def test_jobs_are_isolated_per_principal(harness: AppHarness) -> None:
    job_id = (await harness.submit(headers=ALICE))["id"]
    client = harness.client

    for method, path in [
        ("GET", f"/api/v1/jobs/{job_id}"),
        ("GET", f"/api/v1/jobs/{job_id}/files"),
        ("GET", f"/api/v1/jobs/{job_id}/files/app/main.py"),
        ("GET", f"/api/v1/jobs/{job_id}/events"),
        ("GET", f"/api/v1/jobs/{job_id}/archive"),
        ("GET", f"/api/v1/jobs/{job_id}/architecture"),
        ("POST", f"/api/v1/jobs/{job_id}/cancel"),
        ("DELETE", f"/api/v1/jobs/{job_id}"),
    ]:
        response = await client.request(method, path, headers=BOB)
        assert response.status_code == 404, (method, path)  # not 403: no ID disclosure

    assert (await client.get("/api/v1/jobs", headers=BOB)).json()["items"] == []
    assert len((await client.get("/api/v1/jobs", headers=ALICE)).json()["items"]) == 1


async def test_active_job_limit_is_per_principal(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False, max_active_jobs_per_owner=1)
    await harness.submit(headers=ALICE)
    blocked = await harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=ALICE)
    assert blocked.status_code == 429
    await harness.submit(headers=BOB)


async def test_info_endpoint_is_public_and_reports_auth(harness: AppHarness) -> None:
    body = (await harness.client.get("/api/v1/info")).json()
    assert body["auth_required"] is True
    assert body["providers"] == [{"name": "offline", "model": "offline-templates-v1",
                                  "default": True}]  # fmt: skip


async def test_auth_disabled_mode_uses_anonymous_principal(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(auth_disabled=True, api_keys="", worker_enabled=False)
    job = await harness.submit(headers={})
    assert (await harness.client.get(f"/api/v1/jobs/{job['id']}")).status_code == 200
    assert (await harness.client.get("/api/v1/info")).json()["auth_required"] is False


def test_settings_refuse_to_start_without_keys(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="API_KEYS must contain"):
        make_settings(tmp_path, api_keys="")


@pytest.mark.parametrize(
    "raw", ["no-colon-here-0123456789", "alice:short", ":nameless-key-0123456789"]
)
def test_settings_reject_malformed_keys(tmp_path: Path, raw: str) -> None:
    with pytest.raises(ValidationError, match="principal:key"):
        make_settings(tmp_path, api_keys=raw)


def test_authenticator_unit() -> None:
    auth = ApiKeyAuthenticator({"k" * 20: "alice"})
    assert auth.authenticate("k" * 20) is not None
    assert auth.authenticate("k" * 19) is None
    assert auth.authenticate(None) is None
    assert extract_api_key("Bearer  abc ", None) == "abc"
    assert extract_api_key("bearer abc", None) == "abc"
    assert extract_api_key("Token abc", None) is None
    assert extract_api_key("Bearer x", "header-wins") == "header-wins"
