import pytest

from autoswe.llm.offline.analysis import analyze, extract_entities, infer_field_type
from autoswe.pipeline.naming import kebab, pascal, pluralize, safe_field_name, singularize, snake


def _entities(spec: str) -> dict[str, dict[str, str]]:
    return {e.name: {f.name: f.type for f in e.fields} for e in extract_entities(spec)}


def test_objects_and_plural_subjects_become_entities() -> None:
    found = _entities(
        "A library where members borrow books and librarians track loans with due dates."
    )
    assert list(found) == ["Member", "Book", "Librarian", "Loan"]
    assert found["Book"]["isbn"] == "string"
    assert found["Loan"]["due_date"] == "date"  # hinted field, singularised and typed


def test_explicit_entity_syntax_wins_and_honours_types_and_optionality() -> None:
    entities = extract_entities(
        "Entities: Product(name, price:float, stock:int), Order(status, total:float, notes?)"
    )
    product, order = entities
    assert [(f.name, f.type) for f in product.fields] == [
        ("name", "string"),
        ("price", "float"),
        ("stock", "integer"),
    ]
    notes = next(f for f in order.fields if f.name == "notes")
    assert notes.type == "text"
    assert notes.required is False


def test_reserved_field_names_are_renamed() -> None:
    (entity,) = extract_entities("Widget(id, metadata, label)")
    assert [f.name for f in entity.fields] == ["id_value", "metadata_value", "label"]


def test_spec_without_recognisable_nouns_falls_back_to_item() -> None:
    assert list(_entities("hello world, nothing to see here")) == ["Item"]


def test_entity_count_is_capped() -> None:
    spec = "Track apples, pears, plums, cherries, grapes, melons, lemons and limes."
    assert len(extract_entities(spec)) == 6


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("A task tracker called Taskly: users create tasks.", "taskly"),
        ("An API for a small library where members borrow books.", "library-api"),
        ("Track invoices and payments.", "invoices-service"),
    ],
)
def test_project_name_derivation(spec: str, expected: str) -> None:
    assert analyze(spec).project_name == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("created_at", "datetime"),
        ("due_date", "date"),
        ("is_active", "boolean"),
        ("published", "boolean"),
        ("price", "float"),
        ("stock", "integer"),
        ("owner_id", "integer"),
        ("description", "text"),
        ("title", "string"),
    ],
)
def test_field_type_inference(name: str, expected: str) -> None:
    assert infer_field_type(name) == expected


def test_auth_is_out_of_scope_unless_requested() -> None:
    assert any("Authentication" in item for item in analyze("Manage recipes.").out_of_scope)
    assert not any(
        "Authentication" in item
        for item in analyze("Manage recipes; users must login with a password.").out_of_scope
    )


@pytest.mark.parametrize(
    ("singular", "plural"),
    [("book", "books"), ("category", "categories"), ("box", "boxes"), ("day", "days"),
     ("person", "people"), ("status", "statuses"), ("data", "data")],
)  # fmt: skip
def test_pluralize_and_singularize_round_trip(singular: str, plural: str) -> None:
    assert pluralize(singular) == plural
    assert singularize(plural) == singular


def test_identifier_helpers() -> None:
    assert snake("BookLoan") == "book_loan"
    assert snake("class") == "class_"
    assert snake("3d model") == "n_3d_model"
    assert kebab("Book Loans!") == "book-loans"
    assert pascal("book loan") == "BookLoan"
    assert safe_field_name("ID") == "id_value"
