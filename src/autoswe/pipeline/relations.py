"""Many-to-one relations between entities, shared by every stack's templates.

``Entity.belongs_to`` comes from the analyst, and with a live model it can name an entity that
does not exist, repeat a parent, point at itself, or form a cycle (A belongs to B, B to A). None
of those can become a foreign key that a fresh database accepts in one pass, so the renderers work
from :func:`normalise`, which keeps the first, valid edges and drops the rest.
"""

from __future__ import annotations

from graphlib import TopologicalSorter

from autoswe.pipeline.artifacts import Entity
from autoswe.pipeline.naming import snake


def foreign_key(parent: str) -> str:
    """The child's column for ``parent``: ``Member`` becomes ``member_id``."""
    return f"{snake(parent)}_id"


def normalise(entities: list[Entity]) -> list[Entity]:
    """``entities`` with every ``belongs_to`` limited to known, distinct, acyclic parents.

    Edges are kept in specification order; an edge that would close a cycle is dropped, so the
    result always has a creation order. Entities whose relations are already valid are returned
    unchanged (the same objects).
    """
    known = {entity.name for entity in entities}
    kept: dict[str, list[str]] = {}

    def reaches(start: str, goal: str) -> bool:
        stack, seen = [start], set()
        while stack:
            name = stack.pop()
            if name == goal:
                return True
            if name not in seen:
                seen.add(name)
                stack.extend(kept.get(name, []))
        return False

    result: list[Entity] = []
    for entity in entities:
        parents: list[str] = []
        for parent in entity.belongs_to:
            usable = parent in known and parent != entity.name and parent not in parents
            if usable and not reaches(parent, entity.name):
                parents.append(parent)
                kept[entity.name] = parents
        kept.setdefault(entity.name, parents)
        if parents == entity.belongs_to:
            result.append(entity)
        else:
            result.append(entity.model_copy(update={"belongs_to": parents}))
    return result


def creation_order(entities: list[Entity]) -> list[Entity]:
    """Parents before children; ties keep the specification's order. Expects :func:`normalise`."""
    by_name = {entity.name: entity for entity in entities}
    sorter = TopologicalSorter(
        {e.name: {p for p in e.belongs_to if p in by_name} for e in entities}
    )
    sorter.prepare()
    position = {entity.name: index for index, entity in enumerate(entities)}
    order: list[str] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready(), key=position.__getitem__)
        order.extend(ready)
        sorter.done(*ready)
    return [by_name[name] for name in order]


def children_of(parent: str, entities: list[Entity]) -> list[Entity]:
    """The entities that belong to ``parent``, in specification order."""
    return [entity for entity in entities if parent in entity.belongs_to]
