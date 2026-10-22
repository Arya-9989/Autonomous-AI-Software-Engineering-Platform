"""Token usage, budget and cost for the calling principal, and the pricing table."""

from __future__ import annotations

from fastapi import APIRouter

from autoswe.api.deps import PrincipalDep, ServicesDep
from autoswe.budget import UsageRepository
from autoswe.pricing import usd
from autoswe.schemas import BudgetOut, PricingOut, UsageOut, UsageTotals

router = APIRouter(prefix="/api/v1", tags=["usage"])


@router.get("/usage", response_model=UsageOut)
async def get_usage(principal: PrincipalDep, services: ServicesDep) -> UsageOut:
    """This period's budget and spend for the authenticated principal. Tokens count input
    (cache reads and writes included) plus output; cost comes from the pricing table."""
    async with services.db.session() as session:
        repo = UsageRepository(session)
        state = await repo.state(services.budget, principal.name)
        row = await repo.get(principal.name, state.period_start)
    return UsageOut(
        principal=principal.name,
        budget=BudgetOut(
            period=state.period,
            period_start=state.period_start,
            resets_at=state.resets_at,
            limit_tokens=state.limit,
            used_tokens=state.used,
            reserved_tokens=state.reserved,
            remaining_tokens=state.remaining,
        ),
        usage=UsageTotals(
            input_tokens=row.input_tokens if row else 0,
            output_tokens=row.output_tokens if row else 0,
            cache_read_input_tokens=row.cache_read_input_tokens if row else 0,
            cache_creation_input_tokens=row.cache_creation_input_tokens if row else 0,
            model_calls=row.model_calls if row else 0,
            cost_usd=usd(row.cost_microusd) if row else 0.0,
            cost_complete=not (row and row.unpriced_calls),
            unpriced_calls=row.unpriced_calls if row else 0,
        ),
    )


@router.get("/pricing", response_model=PricingOut)
async def get_pricing(_: PrincipalDep, services: ServicesDep) -> PricingOut:
    """The prices costs are computed from, in USD per million tokens."""
    table = services.pricing
    return PricingOut(
        source=table.source,
        as_of=table.as_of,
        currency=table.currency,
        unit="USD per million tokens",
        models={name: price.as_dict() for name, price in sorted(table.models.items())},
    )
