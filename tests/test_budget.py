"""Token budgets, the usage ledger and cost reporting.

Budgets are enforced with compare-and-set reservations; the concurrency tests submit many jobs
at once and check that exactly as many as fit were accepted.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text, update

from autoswe.budget import BudgetPolicy, UsageRepository, period_bounds
from autoswe.config import Settings
from autoswe.llm.base import LLMRequest, LLMResponse, TokenUsage
from autoswe.metering import MeteredProvider, UsageMeter, price
from autoswe.models import Job, JobStatus, PrincipalUsage
from autoswe.pipeline.artifacts import Requirements, ReviewReport
from autoswe.pricing import PricingError, PricingTable, usd
from autoswe.repository import JobRepository
from tests.conftest import ALICE, BOB, LIBRARY_SPEC, AppHarness, HarnessFactory
from tests.fakes import PricedProvider, ScriptedProvider, registry_with

PRICING = PricingTable.load()
CALL_COST = 5_820  # micro-dollars for PricedProvider's default call on claude-opus-5-5
CALL_TOKENS = 1_200
PIPELINE_CALLS = 5


# --- pricing ------------------------------------------------------------------------------------


def test_cost_counts_uncached_cache_read_cache_write_and_output_at_their_rates() -> None:
    usage = TokenUsage(input_tokens=1_000, output_tokens=200, cache_read_input_tokens=600,
                       cache_creation_input_tokens=100)  # fmt: skip
    assert usage.uncached_input_tokens == 300
    # 300 x $4 + 600 x $0.20 + 100 x $5 + 200 x $20 per million tokens, in micro-dollars
    assert PRICING.cost_microusd("claude-opus-5-5", usage) == CALL_COST
    assert PRICING.cost_microusd("claude-sonnet-5-5", usage) == 300 * 2 + 120 + 250 + 2_000
    assert PRICING.cost_microusd("some-unknown-model", usage) is None
    assert usd(CALL_COST) == 0.00582


def test_the_shipped_table_matches_the_cited_rates() -> None:
    opus = PRICING.models["claude-opus-5-5"]
    assert opus.as_dict() == {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0}
    for model, price_ in PRICING.models.items():
        assert price_.cache_write == price_.input * 5 / 4, model  # 1.25x (5-minute TTL)
    assert "claude-haiku-5-5" not in PRICING.models  # tiered by prompt length: not guessed
    assert PRICING.currency == "USD" and PRICING.as_of == "2026-10-06"


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ({"meta": {"currency": "EUR"}, "models": {}}, "only USD"),
        ({"models": {"m": {"input": 1, "output": 1, "cache_read": 1}}}, "needs exactly"),
        ({"models": {"m": {"input": -1, "output": 1, "cache_read": 1, "cache_write": 1}}},
         "negative"),
        ({"models": {"m": {"input": "x", "output": 1, "cache_read": 1, "cache_write": 1}}},
         "non-numeric"),
    ],
)  # fmt: skip
def test_pricing_tables_are_validated(document: dict[str, Any], match: str) -> None:
    with pytest.raises(PricingError, match=match):
        PricingTable.parse(document)


def test_pricing_file_errors(tmp_path: Path) -> None:
    with pytest.raises(PricingError, match="cannot read"):
        PricingTable.load(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[models\n")
    with pytest.raises(PricingError, match="cannot read"):
        PricingTable.load(bad)


def test_fallback_attempts_are_priced_at_the_model_that_served_each() -> None:
    declined = TokenUsage(input_tokens=1_000, output_tokens=10)
    served = TokenUsage(input_tokens=1_000, output_tokens=100)
    response: LLMResponse[Any] = LLMResponse(
        output=ReviewReport(summary="s", score=1, findings=[]),
        model="claude-opus-4-8",
        usage=declined + served,
        billed=(("claude-opus-5-5", declined), ("claude-opus-4-8", served)),
    )
    priced = price(PRICING, response)
    assert priced.cost_microusd == (1_000 * 4 + 10 * 20) + (1_000 * 5 + 100 * 25)
    assert priced.unpriced_calls == 0
    unknown = LLMResponse(output=response.output, model="x", usage=served)
    assert price(PRICING, unknown).unpriced_calls == 1


def test_token_usage_arithmetic() -> None:
    a = TokenUsage(10, 5, 3, 2, 100, 0)
    b = TokenUsage(1, 1, 1, 1, 1, 1)
    assert a + b == TokenUsage(11, 6, 4, 3, 101, 1)
    assert (a + b) - b == a
    assert a.total_tokens == 15


# --- policy and settings ------------------------------------------------------------------------


def test_period_bounds() -> None:
    now = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
    assert period_bounds("month", now) == (datetime(2026, 12, 1, tzinfo=UTC),
                                           datetime(2027, 1, 1, tzinfo=UTC))  # fmt: skip
    assert period_bounds("day", now) == (datetime(2026, 12, 31, tzinfo=UTC),
                                         datetime(2027, 1, 1, tzinfo=UTC))  # fmt: skip
    assert period_bounds("total", now)[1] is None


def test_policy_limits_and_reservations() -> None:
    policy = BudgetPolicy(default_limit=None, limits={"ci": 0, "alice": 500}, period="month",
                          reservation=1_000)  # fmt: skip
    assert policy.limit_for("bob") is None and policy.reservation_for(None) == 0
    assert policy.reservation_for(500) == 500  # never more than the whole budget
    assert policy.reservation_for(0) == 1  # a zero budget refuses every metered job


@pytest.mark.parametrize("value", ["alice", "alice:", "alice:many", ":5", "alice:-1"])
def test_token_budgets_setting_is_validated(value: str) -> None:
    with pytest.raises(ValueError, match="TOKEN_BUDGETS"):
        Settings(_env_file=None, auth_disabled=True, token_budgets=value)


def test_token_budgets_setting_parses() -> None:
    settings = Settings(_env_file=None, auth_disabled=True, token_budgets=" a:10 , b:0,",
                        token_budget_default="")  # fmt: skip
    assert settings.parsed_token_budgets() == {"a": 10, "b": 0}
    assert settings.token_budget_default is None


# --- end to end through the API ---------------------------------------------------------------


async def _usage(harness: AppHarness, headers: dict[str, str] = ALICE) -> dict[str, Any]:
    response = await harness.client.get("/api/v1/usage", headers=headers)
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


async def _ledger(harness: AppHarness, principal: str = "alice") -> PrincipalUsage:
    async with harness.app.state.services.db.session() as session:
        start, _ = period_bounds("month")
        row = await UsageRepository(session).get(principal, start)
    assert row is not None
    return row


async def test_cost_and_tokens_are_reported_per_job_stage_and_principal(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(registry=registry_with(PricedProvider()))
    job = await harness.submit()
    detail = await harness.wait_for(job["id"], {"succeeded"})

    assert detail["input_tokens"] == PIPELINE_CALLS * 1_000
    assert detail["output_tokens"] == PIPELINE_CALLS * 200
    assert detail["cache_read_input_tokens"] == PIPELINE_CALLS * 600
    assert detail["cache_creation_input_tokens"] == PIPELINE_CALLS * 100
    assert detail["cost_usd"] == usd(PIPELINE_CALLS * CALL_COST) == 0.0291
    assert detail["cost_complete"] is True
    assert {stage["name"]: stage["cost_usd"] for stage in detail["stages"]} == {
        "analyze": 0.00582, "architect": 0.00582, "implement": 0.00582, "devops": 0.00582,
        "review": 0.00582,
    }  # fmt: skip

    usage = await _usage(harness)
    assert usage["principal"] == "alice"
    assert usage["usage"]["model_calls"] == PIPELINE_CALLS
    assert usage["usage"]["cost_usd"] == 0.0291
    assert usage["budget"]["used_tokens"] == PIPELINE_CALLS * CALL_TOKENS
    assert usage["budget"]["limit_tokens"] is None  # unlimited by default
    assert usage["budget"]["reserved_tokens"] == 0
    # Another principal sees only its own (empty) ledger.
    other = await _usage(harness, BOB)
    assert other["usage"]["model_calls"] == 0 and other["budget"]["used_tokens"] == 0


async def test_unpriced_models_are_counted_but_never_given_a_made_up_cost(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(registry=registry_with(ScriptedProvider()))
    job = await harness.submit()
    detail = await harness.wait_for(job["id"], {"succeeded"})
    assert detail["input_tokens"] == PIPELINE_CALLS * 10
    assert detail["cost_usd"] == 0.0 and detail["cost_complete"] is False
    usage = await _usage(harness)
    assert usage["usage"]["unpriced_calls"] == PIPELINE_CALLS
    assert usage["usage"]["cost_complete"] is False


async def test_offline_jobs_are_not_budgeted(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(token_budget_default=0)
    job = await harness.submit()  # a zero budget still allows the free offline provider
    detail = await harness.wait_for(job["id"], {"succeeded"})
    assert detail["input_tokens"] == 0 and detail["cost_usd"] == 0.0
    assert (await _usage(harness))["usage"]["model_calls"] == 0


async def test_submission_beyond_the_budget_is_refused_with_402(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(registry=registry_with(PricedProvider()),
                                    token_budgets="alice:0")  # fmt: skip
    response = await harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=ALICE)
    assert response.status_code == 402
    body = response.json()
    assert body["code"] == "token_budget_exceeded"
    assert "Token budget exhausted: 0 of 0 tokens used" in body["detail"]
    assert body["budget"]["limit_tokens"] == 0 and body["budget"]["remaining_tokens"] == 0
    assert body["budget"]["resets_at"] is not None
    assert (await harness.client.get("/api/v1/jobs", headers=ALICE)).json()["items"] == []
    # Bob has no budget configured: unaffected.
    assert (await harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC},
                                      headers=BOB)).status_code == 202  # fmt: skip
    metrics = (await harness.client.get("/metrics")).text
    assert 'autoswe_budget_rejections_total{where="submit"} 1.0' in metrics


async def test_concurrent_submissions_cannot_overspend(harness_factory: HarnessFactory) -> None:
    """10 submissions race for a budget that fits exactly 3 reservations."""
    harness = await harness_factory(
        registry=registry_with(PricedProvider()), worker_enabled=False,
        token_budget_default=3_000, job_token_reservation=1_000, max_active_jobs_per_owner=50,
    )  # fmt: skip
    responses = await asyncio.gather(*(
        harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=ALICE)
        for _ in range(10)
    ))  # fmt: skip
    codes = sorted(r.status_code for r in responses)
    assert codes == [202] * 3 + [402] * 7
    ledger = await _ledger(harness)
    assert ledger.reserved_tokens == 3_000 and ledger.used_tokens == 0


async def test_a_running_job_stops_when_the_budget_runs_out(
    harness_factory: HarnessFactory,
) -> None:
    # Each call is 1,200 tokens. 1,000 are reserved at submission; the second call tops the
    # reservation up (2,200 <= 3,000); the third cannot (3,400 > 3,000) and the job stops.
    provider = PricedProvider()
    harness = await harness_factory(
        registry=registry_with(provider), token_budget_default=3_000, job_token_reservation=1_000
    )
    job = await harness.submit()
    detail = await harness.wait_for(job["id"], {"failed"})
    assert detail["error_code"] == "token_budget_exceeded"
    assert detail["error"].startswith("Code generator: Token budget exhausted")
    assert [s["status"] for s in detail["stages"]] == [
        "succeeded", "succeeded", "failed", "skipped", "skipped",
    ]  # fmt: skip
    assert provider.calls == 2
    ledger = await _ledger(harness)
    assert ledger.used_tokens == 2 * CALL_TOKENS <= 3_000
    assert ledger.reserved_tokens == 0  # what was left is released when the job ends
    assert detail["input_tokens"] == 2_000  # the job keeps what it spent before failing
    events = (await harness.client.get(f"/api/v1/jobs/{job['id']}/events", headers=ALICE)).text
    assert '"code": "token_budget_exceeded"' in events


async def test_reservations_are_released_on_success_cancel_and_reaping(
    harness_factory: HarnessFactory,
) -> None:
    gate = asyncio.Event()
    provider = PricedProvider(gate=gate)
    harness = await harness_factory(
        registry=registry_with(provider), token_budget_default=1_000_000,
        job_token_reservation=50_000, worker_enabled=False, max_active_jobs_per_owner=10,
    )  # fmt: skip
    queued = await harness.submit()
    stale = await harness.submit()
    assert (await _ledger(harness)).reserved_tokens == 100_000

    # A queued job that is cancelled gives its reservation back at once.
    cancelled = await harness.client.post(f"/api/v1/jobs/{queued['id']}/cancel", headers=ALICE)
    assert cancelled.json()["status"] == "cancelled"
    assert (await _ledger(harness)).reserved_tokens == 50_000

    # A job whose worker vanished is reaped, and its reservation released with it.
    db = harness.app.state.services.db
    async with db.session() as session:
        old = datetime.now(UTC) - timedelta(hours=1)
        await session.execute(update(Job).where(Job.id == stale["id"])
                              .values(status=JobStatus.RUNNING, heartbeat_at=old))  # fmt: skip
        await session.commit()
        assert await JobRepository(session).fail_stale(timedelta(minutes=5)) == [stale["id"]]
    ledger = await _ledger(harness)
    assert ledger.reserved_tokens == 0
    async with db.session() as session:
        tokens = (await session.execute(select(Job.tokens_reserved))).scalars().all()
    assert set(tokens) == {0}
    gate.set()


async def test_a_requeued_job_keeps_what_it_already_spent(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(registry=registry_with(PricedProvider()),
                                    worker_enabled=False)  # fmt: skip
    job = await harness.submit()
    db = harness.app.state.services.db
    async with db.session() as session:
        await session.execute(update(Job).where(Job.id == job["id"]).values(
            status=JobStatus.RUNNING, input_tokens=4_000, cost_microusd=9_000))  # fmt: skip
        await session.commit()
        assert await JobRepository(session).reset_for_retry(job["id"])
    detail = (await harness.client.get(f"/api/v1/jobs/{job['id']}", headers=ALICE)).json()
    assert detail["status"] == "queued"
    assert detail["input_tokens"] == 4_000 and detail["cost_usd"] == 0.009


async def test_pricing_endpoint(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    assert (await harness.client.get("/api/v1/pricing")).status_code == 401
    body = (await harness.client.get("/api/v1/pricing", headers=ALICE)).json()
    assert body["unit"] == "USD per million tokens"
    assert body["models"]["claude-opus-5-5"]["cache_read"] == 0.2
    assert (await harness.client.get("/api/v1/usage")).status_code == 401


async def test_metering_without_a_guard_prices_and_totals_calls() -> None:
    meter = UsageMeter(TokenUsage(input_tokens=5))
    metered = MeteredProvider(PricedProvider(), PRICING, meter)
    request = LLMRequest(agent="analyst", system="s", prompt="p", output_type=Requirements,
                         context={"spec": LIBRARY_SPEC})  # fmt: skip
    response = await metered.generate(request)
    assert response.usage.cost_microusd == CALL_COST
    assert meter.total.input_tokens == 1_005 and meter.calls == 1


# --- PostgreSQL ---------------------------------------------------------------------------------

POSTGRES_URL = os.environ.get("AUTOSWE_TEST_POSTGRES_URL")


@pytest.mark.skipif(POSTGRES_URL is None, reason="set AUTOSWE_TEST_POSTGRES_URL to run")
async def test_concurrent_submissions_cannot_overspend_on_postgres(
    harness_factory: HarnessFactory,
) -> None:
    """The same race on PostgreSQL, where transactions really run in parallel."""
    from autoswe.db import Database
    from autoswe.migrations.runner import upgrade_to_head

    assert POSTGRES_URL is not None
    await asyncio.to_thread(upgrade_to_head, POSTGRES_URL)
    db = Database(POSTGRES_URL)
    async with db.session() as session:
        await session.execute(text("TRUNCATE jobs, principal_usage CASCADE"))
        await session.commit()
    await db.dispose()
    harness = await harness_factory(
        registry=registry_with(PricedProvider()), worker_enabled=False, database_url=POSTGRES_URL,
        token_budget_default=5_000, job_token_reservation=1_000, max_active_jobs_per_owner=100,
    )  # fmt: skip
    responses = await asyncio.gather(*(
        harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=ALICE)
        for _ in range(25)
    ))  # fmt: skip
    assert sorted(r.status_code for r in responses) == [202] * 5 + [402] * 20
    ledger = await _ledger(harness)
    assert ledger.reserved_tokens == 5_000
