"""Per-principal token budgets, enforced server-side with reservations.

A budget is a number of tokens (input, cache included, plus output) a principal may spend per
period. Enforcing it is a concurrency problem: two jobs submitted together must not both pass a
check that only one of them fits under. So nothing ever *checks and then spends*:

1. Submitting a job reserves ``JOB_TOKEN_RESERVATION`` tokens with one compare-and-set
   ``UPDATE principal_usage SET reserved = reserved + :n WHERE used + reserved + :n <= :limit``
   in the same transaction that inserts the job. Of two racing submissions only one row update
   can satisfy the condition last; the other is refused with HTTP 402.
2. Each model call converts reservation into usage. When a job has used its reservation, the
   next call first tops it up with the same compare-and-set; if that fails the job stops with
   ``token_budget_exceeded``. A call already in flight is allowed to finish, so a principal can
   overshoot by at most one call's tokens.
3. Whatever a job has not used is released in the transaction that ends it (success, failure,
   cancellation or the stale-job reaper), so reservations cannot leak.

A job's usage counts in the period it was submitted in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import case, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from autoswe.config import Settings
from autoswe.db import Database
from autoswe.llm.base import LLMError, TokenUsage
from autoswe.models import Job, PrincipalUsage, utcnow
from autoswe.observability import BUDGET_REJECTIONS

Period = Literal["month", "day", "total"]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
BUDGET_EXCEEDED = "token_budget_exceeded"


def period_bounds(kind: Period, now: datetime | None = None) -> tuple[datetime, datetime | None]:
    """The current period's start and the moment it resets (``None`` for a lifetime budget)."""
    now = (now or utcnow()).astimezone(UTC)
    if kind == "total":
        return EPOCH, None
    if kind == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, start.replace(year=year, month=month)


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    default_limit: int | None
    limits: dict[str, int]
    period: Period
    reservation: int

    @classmethod
    def from_settings(cls, settings: Settings) -> BudgetPolicy:
        return cls(
            default_limit=settings.token_budget_default,
            limits=settings.parsed_token_budgets(),
            period=settings.token_budget_period,
            reservation=settings.job_token_reservation,
        )

    def limit_for(self, principal: str) -> int | None:
        return self.limits.get(principal, self.default_limit)

    def reservation_for(self, limit: int | None) -> int:
        # At least one token, so a zero budget refuses every metered job.
        return 0 if limit is None else max(1, min(self.reservation, limit))


@dataclass(frozen=True, slots=True)
class BudgetState:
    principal: str
    period: Period
    period_start: datetime
    resets_at: datetime | None
    limit: int | None
    used: int
    reserved: int

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used - self.reserved, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "principal": self.principal,
            "period": self.period,
            "period_start": self.period_start.isoformat(),
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "limit_tokens": self.limit,
            "used_tokens": self.used,
            "reserved_tokens": self.reserved,
            "remaining_tokens": self.remaining,
        }


class TokenBudgetExceededError(LLMError):
    code = BUDGET_EXCEEDED

    def __init__(self, message: str, state: BudgetState | None = None) -> None:
        super().__init__(message)
        self.state = state


def _insert_ignore(session: AsyncSession, principal: str, period_start: datetime) -> Any:
    values = {
        "principal": principal, "period_start": period_start, "used_tokens": 0,
        "reserved_tokens": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cost_microusd": 0,
        "unpriced_calls": 0, "model_calls": 0, "updated_at": utcnow(),
    }  # fmt: skip
    dialect = session.get_bind().dialect.name
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    return (
        insert(PrincipalUsage)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["principal", "period_start"])
    )


def _decrement(column: Any, amount: int) -> Any:
    """``column - amount``, never below zero (a reaped job's worker may still be reporting)."""
    return case((column >= amount, column - amount), else_=0)


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure(self, principal: str, period_start: datetime) -> None:
        await self.session.execute(_insert_ignore(self.session, principal, period_start))

    async def try_reserve(
        self, principal: str, period_start: datetime, amount: int, limit: int
    ) -> bool:
        """The compare-and-set at the heart of budget enforcement. Does not commit."""
        row = PrincipalUsage
        result = await self.session.execute(
            update(row)
            .where(
                row.principal == principal,
                row.period_start == period_start,
                row.used_tokens + row.reserved_tokens + amount <= limit,
            )
            .values(reserved_tokens=row.reserved_tokens + amount, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def get(self, principal: str, period_start: datetime) -> PrincipalUsage | None:
        query = (
            select(PrincipalUsage)
            .where(PrincipalUsage.principal == principal,
                   PrincipalUsage.period_start == period_start)
            .execution_options(populate_existing=True)
        )  # fmt: skip
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list(self, principal: str | None = None) -> list[PrincipalUsage]:
        query = select(PrincipalUsage).order_by(
            PrincipalUsage.period_start.desc(), PrincipalUsage.principal
        )
        if principal is not None:
            query = query.where(PrincipalUsage.principal == principal)
        return list((await self.session.execute(query)).scalars().all())

    async def extend(
        self, job_id: str, principal: str, period_start: datetime, amount: int, limit: int
    ) -> bool:
        """Top up a running job's reservation; commits."""
        if not await self.try_reserve(principal, period_start, amount, limit):
            await self.session.rollback()
            return False
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(tokens_reserved=Job.tokens_reserved + amount)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        return True

    async def record(
        self, job_id: str, principal: str, period_start: datetime, usage: TokenUsage,
        consumed: int,
    ) -> None:  # fmt: skip
        """Add one call's usage to the ledger, converting ``consumed`` reserved tokens."""
        row = PrincipalUsage
        await self.session.execute(
            update(row)
            .where(row.principal == principal, row.period_start == period_start)
            .values(
                used_tokens=row.used_tokens + usage.total_tokens,
                reserved_tokens=_decrement(row.reserved_tokens, consumed),
                input_tokens=row.input_tokens + usage.input_tokens,
                output_tokens=row.output_tokens + usage.output_tokens,
                cache_read_input_tokens=row.cache_read_input_tokens + usage.cache_read_input_tokens,
                cache_creation_input_tokens=row.cache_creation_input_tokens
                + usage.cache_creation_input_tokens,
                cost_microusd=row.cost_microusd + usage.cost_microusd,
                unpriced_calls=row.unpriced_calls + usage.unpriced_calls,
                model_calls=row.model_calls + 1,
                updated_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        if consumed:
            await self.session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(tokens_reserved=_decrement(Job.tokens_reserved, consumed))
                .execution_options(synchronize_session=False)
            )
        await self.session.commit()

    async def release(self, job_id: str) -> None:
        """Give back what a job still holds. Runs inside the caller's transaction."""
        held = (
            await self.session.execute(
                select(Job.owner, Job.budget_period_start, Job.tokens_reserved).where(
                    Job.id == job_id
                )
            )
        ).one_or_none()
        if held is None or not held.tokens_reserved or held.budget_period_start is None:
            return
        row = PrincipalUsage
        await self.session.execute(
            update(row)
            .where(row.principal == held.owner, row.period_start == held.budget_period_start)
            .values(reserved_tokens=_decrement(row.reserved_tokens, held.tokens_reserved),
                    updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )  # fmt: skip
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(tokens_reserved=0)
            .execution_options(synchronize_session=False)
        )

    async def state(self, policy: BudgetPolicy, principal: str) -> BudgetState:
        start, resets_at = period_bounds(policy.period)
        row = await self.get(principal, start)
        return BudgetState(
            principal=principal, period=policy.period, period_start=start, resets_at=resets_at,
            limit=policy.limit_for(principal), used=row.used_tokens if row else 0,
            reserved=row.reserved_tokens if row else 0,
        )  # fmt: skip


class BudgetGuard:
    """One running job's view of its principal's budget (used by the metering layer)."""

    def __init__(
        self,
        db: Database,
        policy: BudgetPolicy,
        *,
        job_id: str,
        principal: str,
        period_start: datetime,
        reserved: int,
    ) -> None:
        self._db = db
        self._policy = policy
        self.job_id = job_id
        self.principal = principal
        self.period_start = period_start
        self.limit = policy.limit_for(principal)
        self.reserved = reserved

    async def before_call(self) -> None:
        if self.limit is None or self.reserved > 0:
            return
        amount = self._policy.reservation_for(self.limit)
        async with self._db.session() as session:
            repo = UsageRepository(session)
            if await repo.extend(self.job_id, self.principal, self.period_start, amount,
                                 self.limit):  # fmt: skip
                self.reserved += amount
                return
            row = await repo.get(self.principal, self.period_start)
        BUDGET_REJECTIONS.labels("run").inc()
        used = row.used_tokens if row else 0
        raise TokenBudgetExceededError(
            f"Token budget exhausted: {self.principal} has used {used} of {self.limit} tokens "
            "this period"
        )

    async def after_call(self, usage: TokenUsage) -> None:
        consumed = min(usage.total_tokens, self.reserved)
        self.reserved -= consumed
        async with self._db.session() as session:
            await UsageRepository(session).record(
                self.job_id, self.principal, self.period_start, usage, consumed
            )
