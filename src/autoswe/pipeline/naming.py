"""Identifier helpers shared by the offline engine, validators and docs renderer."""

from __future__ import annotations

import keyword
import re

_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Names that would shadow SQLAlchemy/Pydantic internals or columns every model already has.
RESERVED_FIELD_NAMES = frozenset(
    {"id", "created_at", "metadata", "registry", "model_config", "model_fields", "query"}
)

_IRREGULAR_PLURALS = {
    "person": "people",
    "child": "children",
    "man": "men",
    "woman": "women",
    "mouse": "mice",
    "goose": "geese",
    "datum": "data",
}
_IRREGULAR_SINGULARS = {plural: singular for singular, plural in _IRREGULAR_PLURALS.items()}
_UNCOUNTABLE = frozenset({"data", "news", "equipment", "information", "feedback", "inventory"})


def words(value: str) -> list[str]:
    spaced = _CAMEL_BOUNDARY.sub(" ", value)
    return [part.lower() for part in _NON_ALNUM.split(spaced) if part]


def snake(value: str) -> str:
    result = "_".join(words(value)) or "item"
    if result[0].isdigit():
        result = f"n_{result}"
    if keyword.iskeyword(result):
        result = f"{result}_"
    return result


def kebab(value: str) -> str:
    return "-".join(words(value)) or "project"


def pascal(value: str) -> str:
    return "".join(word.capitalize() for word in words(value)) or "Item"


def pluralize(word: str) -> str:
    lower = word.lower()
    if lower in _UNCOUNTABLE:
        return word
    if lower in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[lower]
    if re.search(r"[^aeiou]y$", lower):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", lower):
        return word + "es"
    return word + "s"


def singularize(word: str) -> str:  # noqa: PLR0911 - a rule table reads best as early returns
    lower = word.lower()
    if lower in _UNCOUNTABLE:
        return word
    if lower in _IRREGULAR_SINGULARS:
        return _IRREGULAR_SINGULARS[lower]
    if lower.endswith("ies") and len(lower) > 4:
        return word[:-3] + "y"
    if re.search(r"(ss|us|is)$", lower):
        return word
    # "statuses" -> "status", "boxes" -> "box"; but "houses" -> "house" (vowel before -uses)
    if re.search(r"(ches|shes|xes|zes|sses|[^aeiou]uses)$", lower):
        return word[:-2]
    if lower.endswith("s") and len(lower) > 3:
        return word[:-1]
    return word


def safe_field_name(name: str) -> str:
    candidate = snake(name)
    return f"{candidate}_value" if candidate in RESERVED_FIELD_NAMES else candidate
