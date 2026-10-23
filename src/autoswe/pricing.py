"""The pricing table (``pricing.toml``) and the cost of a model call.

Prices are USD per million tokens, which is the same number as micro-dollars per token, so a
call's cost in micro-dollars is a sum of token counts times rates, computed with ``Decimal``
and rounded once per call. A model missing from the table is *unpriced*: its tokens are still
counted and budgeted, and costs that include it are reported as incomplete, never estimated.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from autoswe.llm.base import TokenUsage

DEFAULT_PRICING_FILE = Path(__file__).with_name("pricing.toml")
_RATES = ("input", "output", "cache_read", "cache_write")


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal

    def as_dict(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in _RATES}


class PricingError(ValueError):
    pass


@dataclass(frozen=True)
class PricingTable:
    source: str
    as_of: str
    currency: str
    models: dict[str, ModelPrice]

    @classmethod
    def load(cls, path: Path | None = None) -> PricingTable:
        target = path or DEFAULT_PRICING_FILE
        try:
            document = tomllib.loads(target.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PricingError(f"cannot read pricing table {target}: {exc}") from exc
        return cls.parse(document, origin=str(target))

    @classmethod
    def parse(cls, document: dict[str, Any], origin: str = "<pricing>") -> PricingTable:
        meta = document.get("meta") or {}
        if meta.get("currency", "USD") != "USD":
            raise PricingError(f"{origin}: only USD prices are supported")
        models: dict[str, ModelPrice] = {}
        for model, rates in (document.get("models") or {}).items():
            if not isinstance(rates, dict) or set(rates) != set(_RATES):
                raise PricingError(f"{origin}: {model} needs exactly {', '.join(_RATES)}")
            try:
                values = {name: Decimal(str(rates[name])) for name in _RATES}
            except InvalidOperation as exc:
                raise PricingError(f"{origin}: {model} has a non-numeric rate") from exc
            if any(value < 0 or not value.is_finite() for value in values.values()):
                raise PricingError(f"{origin}: {model} has a negative or non-finite rate")
            models[model] = ModelPrice(**values)
        return cls(
            source=str(meta.get("source", origin)),
            as_of=str(meta.get("as_of", "")),
            currency="USD",
            models=models,
        )

    def cost_microusd(self, model: str, usage: TokenUsage) -> int | None:
        """The call's cost in micro-dollars, or ``None`` when the model is not priced."""
        price = self.models.get(model)
        if price is None:
            return None
        total = (
            Decimal(usage.uncached_input_tokens) * price.input
            + Decimal(usage.cache_read_input_tokens) * price.cache_read
            + Decimal(usage.cache_creation_input_tokens) * price.cache_write
            + Decimal(usage.output_tokens) * price.output
        )
        return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def usd(microusd: int) -> float:
    """Micro-dollars as dollars, for JSON (six decimals: exact for any stored value)."""
    return round(microusd / 1_000_000, 6)
