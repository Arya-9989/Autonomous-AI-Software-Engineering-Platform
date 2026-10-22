"""Metering: price every model call, keep running totals, and enforce the budget.

:class:`MeteredProvider` wraps the provider a job (or a CLI or eval run) uses. Before each call
it asks the :class:`~autoswe.budget.BudgetGuard` for headroom; after it, it prices the call from
the pricing table (per billed attempt, so a server-side fallback is priced at the model that
served each attempt), adds it to a :class:`UsageMeter`, and reports it to the guard's ledger.
"""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel

from autoswe.budget import BudgetGuard
from autoswe.llm.base import LLMProvider, LLMRequest, LLMResponse, TokenUsage, is_metered
from autoswe.observability import LLM_COST, LLM_TOKENS
from autoswe.pricing import PricingTable


class UsageMeter:
    """Running totals for one job (cumulative across a requeue: it starts from what the job had
    already spent)."""

    def __init__(self, start: TokenUsage | None = None) -> None:
        self.total = start or TokenUsage()
        self.calls = 0

    def add(self, usage: TokenUsage) -> None:
        self.total += usage
        self.calls += 1


def price(pricing: PricingTable, response: LLMResponse[BaseModel]) -> TokenUsage:
    entries = response.billed or ((response.model, response.usage),)
    cost = 0
    unpriced = False
    for model, usage in entries:
        call_cost = pricing.cost_microusd(model, usage)
        if call_cost is None:
            unpriced = True
        else:
            cost += call_cost
    return dataclasses.replace(response.usage, cost_microusd=cost, unpriced_calls=int(unpriced))


class MeteredProvider:
    def __init__(
        self,
        inner: LLMProvider,
        pricing: PricingTable,
        meter: UsageMeter | None = None,
        guard: BudgetGuard | None = None,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.metered = is_metered(inner)
        self._pricing = pricing
        self.meter = meter or UsageMeter()
        self._guard = guard if self.metered else None

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        if self._guard is not None:
            await self._guard.before_call()
        response = await self.inner.generate(request)
        if not self.metered:  # the offline provider: nothing billed, nothing to price
            self.meter.add(response.usage)
            return response
        usage = price(self._pricing, response)
        self.meter.add(usage)
        if self._guard is not None:
            await self._guard.after_call(usage)
        for direction, value in (
            ("input", usage.input_tokens),
            ("output", usage.output_tokens),
            ("cache_read", usage.cache_read_input_tokens),
            ("cache_write", usage.cache_creation_input_tokens),
        ):
            if value:
                LLM_TOKENS.labels(self.name, direction).inc(value)
        if usage.cost_microusd:
            LLM_COST.labels(self.name, response.model).inc(usage.cost_microusd / 1_000_000)
        return dataclasses.replace(response, usage=usage)
