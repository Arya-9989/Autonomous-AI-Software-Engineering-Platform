"""Deterministic requirements extraction used by the offline provider.

This is intentionally a small, transparent heuristic, not an NLP system. It recognises
domain nouns that follow common verbs ("manage books", "track loans") and an explicit
``Entity(field, field:type)`` syntax that lets users pin down the data model precisely.
"""

from __future__ import annotations

import re

from autoswe.pipeline.artifacts import Entity, EntityField, FieldType, Requirements
from autoswe.pipeline.naming import kebab, pascal, pluralize, safe_field_name, singularize, words

MAX_ENTITIES = 6

_VERBS = frozenset(
    """
    manage manages managing track tracks tracking store stores storing create creates list
    lists browse browses schedule schedules organize organizes organise organises add adds
    register registers catalog catalogue submit submits rate rates assign assigns borrow
    borrows sell sells buy buys publish publishes upload uploads share shares monitor
    monitors maintain maintains handle handles collect collects reserve reserves invite
    invites follow follows keep keeps
    """.split()
)
_SKIP = frozenset(
    """
    a an the their its his her our my your new existing all any each every multiple many
    several some own other upcoming recent past simple basic small large various different
    and or of to for with by on in at from about into own them it this that these those
    and/or can should must will may be is are was were
    """.split()
)
_NOT_ENTITIES = frozenset(
    """
    data api apis rest service services app application applications system systems way ways
    time things thing stuff information details detail people users' crud endpoint endpoints
    database databases backend frontend platform them it track list status progress history
    """.split()
)

_CLAUSE_WORDS = frozenset(
    "where which who whom whose that when while with to so in and or but".split()
)

_KNOWN_FIELDS: dict[str, list[tuple[str, FieldType, bool]]] = {
    "book": [
        ("title", "string", True),
        ("author", "string", True),
        ("isbn", "string", False),
        ("published_year", "integer", False),
    ],
    "member": [("name", "string", True), ("email", "string", True)],
    "user": [("name", "string", True), ("email", "string", True)],
    "customer": [("name", "string", True), ("email", "string", True), ("phone", "string", False)],
    "author": [("name", "string", True), ("bio", "text", False)],
    "loan": [
        ("borrowed_on", "date", True),
        ("due_date", "date", True),
        ("returned", "boolean", False),
    ],
    "task": [
        ("title", "string", True),
        ("description", "text", False),
        ("done", "boolean", False),
        ("due_date", "date", False),
    ],
    "project": [("name", "string", True), ("description", "text", False)],
    "note": [("title", "string", True), ("body", "text", False)],
    "product": [
        ("name", "string", True),
        ("price", "float", True),
        ("stock", "integer", False),
    ],
    "order": [("status", "string", True), ("total", "float", True)],
    "invoice": [("number", "string", True), ("amount", "float", True), ("paid", "boolean", False)],
    "event": [
        ("title", "string", True),
        ("starts_at", "datetime", True),
        ("location", "string", False),
    ],
    "appointment": [
        ("starts_at", "datetime", True),
        ("duration_minutes", "integer", True),
        ("notes", "text", False),
    ],
    "reservation": [("starts_at", "datetime", True), ("party_size", "integer", True)],
    "post": [("title", "string", True), ("body", "text", True), ("published", "boolean", False)],
    "comment": [("author", "string", True), ("body", "text", True)],
    "expense": [
        ("description", "string", True),
        ("amount", "float", True),
        ("spent_on", "date", True),
    ],
    "ticket": [
        ("title", "string", True),
        ("priority", "string", False),
        ("resolved", "boolean", False),
    ],
    "recipe": [
        ("name", "string", True),
        ("instructions", "text", True),
        ("servings", "integer", False),
    ],
    "patient": [("name", "string", True), ("date_of_birth", "date", True)],
    "employee": [("name", "string", True), ("email", "string", True), ("role", "string", False)],
}

# Many-to-one links the heuristics add when both entities are present ("loans" of "members").
_KNOWN_RELATIONS: dict[str, list[str]] = {
    "loan": ["member", "book"],
    "comment": ["post"],
    "ticket": ["event", "attendee"],
    "event": ["organizer"],
    "pet": ["owner"],
    "order": ["customer"],
    "invoice": ["customer"],
    "task": ["project"],
    "appointment": ["patient", "pet", "vet"],
    "reservation": ["customer"],
}

_EXPLICIT_ENTITY = re.compile(r"\b([A-Z][A-Za-z0-9]+)\s*\(([^()]*)\)")
_PROJECT_NAME = re.compile(
    r"\b(?:called|named)\s+[\"'`]?([A-Za-z][A-Za-z0-9 _-]{1,40}?)[\"'`]?"
    r"(?=[,.;:]|\s+(?:that|which|for|to|where)\b|$)"
)
_PURPOSE = re.compile(r"\bfor\s+(?:a|an|the|our|my)\s+((?:[a-z]+\s){0,2}[a-z]+)")
_WITH_FIELDS = (
    r"\b{name}\s+(?:with|having|that have|including)\s+(?:a |an |their |its )?([a-z_ ,]+?)"
    r"(?=[.;:!?]|$|\s+(?:and\s+)?(?:a|an|the|where|who|which|so)\s)"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def infer_field_type(name: str) -> FieldType:
    rules: list[tuple[str, FieldType]] = [
        (r"(_at|_time|timestamp)$", "datetime"),
        (r"(date|_on|birthday|deadline)$|^date", "date"),
        (
            r"^(is|has|can)_"
            r"|^(done|completed|active|enabled|paid|published|returned|resolved|archived)$",
            "boolean",
        ),
        (r"(price|amount|total|cost|rate|balance|score|weight|latitude|longitude)$", "float"),
        (
            r"(count|quantity|qty|stock|year|age|size|minutes|seconds"
            r"|number_of_\w+|_id|level|rank|servings)$",
            "integer",
        ),
        (r"(description|notes|body|content|bio|summary|instructions|comment)$", "text"),
    ]
    for pattern, field_type in rules:
        if re.search(pattern, name):
            return field_type
    return "string"


def _parse_field_list(
    raw: str, entity_names: frozenset[str] = frozenset(), relations: list[str] | None = None
) -> list[EntityField]:
    """Fields from ``name, name:type, name?`` syntax; ``name:Entity`` (another declared entity)
    is a relation and goes to ``relations`` instead of becoming a field."""
    fields: list[EntityField] = []
    seen: set[str] = set()
    for chunk in re.split(r",|\band\b", raw):
        token = chunk.strip()
        if not token:
            continue
        name_part, _, type_part = token.partition(":")
        required = not name_part.strip().endswith("?")
        raw_name = name_part.strip().rstrip("?")
        parts = words(raw_name)
        if len(raw_name.split()) > 1:  # prose like "due dates" -> "due_date"; keep snake_case as-is
            parts[-1] = singularize(parts[-1])
        name = safe_field_name("_".join(parts))
        if not name or name in seen:
            continue
        if type_part.strip() in entity_names and relations is not None:
            if type_part.strip() not in relations:
                relations.append(type_part.strip())
            continue
        explicit = type_part.strip().lower()
        aliases: dict[str, FieldType] = {
            "str": "string", "string": "string", "text": "text", "int": "integer",
            "integer": "integer", "float": "float", "number": "float", "decimal": "float",
            "bool": "boolean", "boolean": "boolean", "date": "date", "datetime": "datetime",
            "timestamp": "datetime",
        }  # fmt: skip
        field_type = aliases.get(explicit, infer_field_type(name))
        fields.append(EntityField(name=name, type=field_type, required=required))
        seen.add(name)
    return fields


def _default_fields(entity_key: str) -> list[EntityField]:
    known = _KNOWN_FIELDS.get(entity_key)
    if known:
        return [EntityField(name=n, type=t, required=r) for n, t, r in known]
    return [
        EntityField(name="name", type="string", required=True),
        EntityField(name="description", type="text", required=False),
    ]


def _is_noun(word: str) -> bool:
    return (
        word.isalpha()
        and len(word) >= 3
        and word not in _SKIP
        and word not in _VERBS
        and word not in _CLAUSE_WORDS
    )


def _candidate_nouns(spec: str) -> list[str]:
    """Nouns that are objects of a known verb ("track loans") or plural subjects of one
    ("members borrow"), in order of appearance."""
    tokens = [t.lower().strip("'") for t in re.findall(r"[A-Za-z][A-Za-z'-]*|[.,;:!?]", spec)]
    found: list[str] = []

    def add(word: str) -> None:
        if word not in _NOT_ENTITIES:
            found.append(singularize(word))

    for index, token in enumerate(tokens):
        if token not in _VERBS:
            continue
        previous = tokens[index - 1] if index else ""
        if previous.endswith("s") and _is_noun(previous):
            add(previous)
        cursor = index + 1
        while cursor < len(tokens):
            word = tokens[cursor]
            if word in {"and", "or", ","} or word in _SKIP:
                cursor += 1
                continue
            if not _is_noun(word):
                break
            following = tokens[cursor + 1] if cursor + 1 < len(tokens) else ""
            if following in _VERBS:  # "... and librarians track": a new clause's subject
                break
            add(word)
            if following in {"and", "or", ","}:  # "books and loans": keep collecting
                cursor += 1
                continue
            break
    return found


def extract_entities(spec: str) -> list[Entity]:
    entities: dict[str, Entity] = {}

    matches = list(_EXPLICIT_ENTITY.finditer(spec))
    declared = frozenset(pascal(singularize(m.group(1))) for m in matches)
    for match in matches:
        name = pascal(singularize(match.group(1)))
        relations: list[str] = []
        fields = _parse_field_list(match.group(2), declared - {name}, relations)
        fields = fields or _default_fields(name.lower())
        entities.setdefault(
            name, Entity(name=name, description=f"{name} records.", fields=fields,
                         belongs_to=relations)
        )  # fmt: skip

    if not entities:
        for noun in _candidate_nouns(spec):
            name = pascal(noun)
            if name in entities or len(entities) >= MAX_ENTITIES:
                continue
            fields = _default_fields(noun.lower())
            hint = re.search(
                _WITH_FIELDS.format(name=f"(?:{re.escape(noun)}|{re.escape(pluralize(noun))})"),
                spec,
                flags=re.IGNORECASE,
            )
            if hint:
                hinted = _parse_field_list(hint.group(1))
                if hinted:  # the spec's own fields first, then known defaults it did not name
                    names = {f.name for f in hinted}
                    fields = hinted + [f for f in fields if f.name not in names]
            entities[name] = Entity(
                name=name, description=f"{pluralize(name)} managed by the service.", fields=fields
            )

    if entities and not matches:  # prose: link entities the domain knowledge relates
        for name, entity in list(entities.items()):
            parents = [pascal(p) for p in _KNOWN_RELATIONS.get(name.lower(), [])]
            linked = [parent for parent in parents if parent in entities]
            if linked:
                entities[name] = entity.model_copy(update={"belongs_to": linked})

    if not entities:
        entities["Item"] = Entity(
            name="Item", description="Generic records managed by the service.",
            fields=_default_fields("item"),
        )  # fmt: skip
    return list(entities.values())[:MAX_ENTITIES]


def derive_project_name(spec: str, entities: list[Entity]) -> str:
    named = _PROJECT_NAME.search(spec)
    if named:
        return kebab(named.group(1))[:48]
    purpose = _PURPOSE.search(spec.lower())
    if purpose:
        phrase: list[str] = []
        for word in words(purpose.group(1)):
            if word in _CLAUSE_WORDS:
                break
            phrase.append(word)
        meaningful = [w for w in phrase if w not in _SKIP]
        if meaningful:
            return kebab(" ".join(meaningful[-2:]) + " api")
    return kebab(pluralize(entities[0].name) + " service")


def analyze(spec: str) -> Requirements:
    entities = extract_entities(spec)
    sentences = [s.strip(" -*\t") for s in _SENTENCE_SPLIT.split(spec.strip()) if s.strip()]
    functional = [s for s in sentences if len(s) > 12][:12] or [spec.strip()[:300]]
    for entity in entities:
        functional.append(
            f"Expose create, read, update, delete and paginated list operations for "
            f"{pluralize(entity.name).lower()}."
        )
    lowered = spec.lower()
    non_functional = [
        "Ship as a non-root container image with a health check.",
        "Every change runs lint, tests and an image build in CI.",
        "Configuration comes from environment variables (12-factor).",
    ]
    if any(word in lowered for word in ("scale", "scalable", "traffic", "concurrent")):
        non_functional.append("Run stateless replicas behind a load balancer.")
    out_of_scope = []
    if not any(word in lowered for word in ("auth", "login", "password", "sign in", "jwt")):
        out_of_scope.append("Authentication and authorisation (not requested).")
    out_of_scope.append("A graphical frontend; the deliverable is an HTTP API.")

    return Requirements(
        project_name=derive_project_name(spec, entities),
        summary=(sentences[0] if sentences else spec.strip())[:280],
        user_stories=[
            f"As an API client, I can manage {pluralize(e.name).lower()} "
            "(create, list, view, update, delete)."
            for e in entities
        ],
        functional_requirements=functional,
        non_functional_requirements=non_functional,
        entities=entities,
        assumptions=[
            "Single-tenant HTTP JSON API.",
            "SQLite for local development, PostgreSQL in production via DATABASE_URL.",
        ],
        out_of_scope=out_of_scope,
    )
