"""Entity relationships and Alembic migrations in generated projects.

The analyst finds many-to-one links (``Entity.belongs_to``); every stack turns them into
required ``<parent>_id`` keys that must point at an existing parent, can filter lists, and
cascade on delete. The Python project's schema is owned by Alembic, and the generated project
tests its own migrations: these tests generate projects and run their suites for real.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from autoswe.evals.fixtures import EvalFixture, Expectations
from autoswe.evals.scoring import score
from autoswe.llm.offline import OfflineProvider
from autoswe.llm.offline import templates_migrations as migrations
from autoswe.llm.offline.analysis import extract_entities
from autoswe.pipeline import relations
from autoswe.pipeline.agents import PipelineState, run_pipeline
from autoswe.pipeline.artifacts import Entity, EntityField
from autoswe.stacks import Stack
from tests.conftest import LIBRARY_SPEC

POSTGRES_URL = os.environ.get("AUTOSWE_TEST_POSTGRES_URL")


def _entity(name: str, *parents: str) -> Entity:
    return Entity(
        name=name,
        description=f"{name} records.",
        fields=[EntityField(name="name", type="string", required=True)],
        belongs_to=list(parents),
    )


# --- analysis --------------------------------------------------------------------------------


def test_prose_links_entities_the_domain_relates() -> None:
    by_name = {e.name: e for e in extract_entities(LIBRARY_SPEC)}
    assert by_name["Loan"].belongs_to == ["Member", "Book"]
    assert by_name["Member"].belongs_to == [] and by_name["Book"].belongs_to == []


def test_a_known_link_needs_both_entities() -> None:
    full = extract_entities(
        "An events platform where organizers publish events and attendees buy tickets."
    )
    assert {e.name: e.belongs_to for e in full} == {
        "Organizer": [], "Event": ["Organizer"], "Attendee": [], "Ticket": ["Event", "Attendee"],
    }  # fmt: skip
    partial = {e.name: e.belongs_to for e in extract_entities("Attendees buy tickets.")}
    assert partial["Ticket"] == ["Attendee"]  # no Event, so no Ticket -> Event


def test_explicit_entity_typed_fields_become_relations() -> None:
    entities = extract_entities(
        "Entities: Post(title, body:text), Comment(post:Post, author, approved:bool)"
    )
    comment = next(e for e in entities if e.name == "Comment")
    assert comment.belongs_to == ["Post"]
    assert [f.name for f in comment.fields] == ["author", "approved"]  # no "post" column


def test_explicit_specs_get_no_guessed_links() -> None:
    # "comment -> post" is known domain knowledge, but an explicit spec says what it means.
    entities = extract_entities("Entities: Post(title), Comment(author)")
    assert all(not e.belongs_to for e in entities)


# --- normalising and ordering ------------------------------------------------------------


def test_normalise_drops_unknown_self_duplicate_and_cyclic_links() -> None:
    entities = [
        _entity("A", "B", "B", "A", "Ghost"),
        _entity("B", "A"),  # would close A -> B -> A
        _entity("C", "A", "B"),
    ]
    result = relations.normalise(entities)
    assert [e.belongs_to for e in result] == [["B"], [], ["A", "B"]]
    assert result[2] is entities[2]  # valid entities are returned untouched


def test_creation_order_puts_parents_first_and_keeps_ties_stable() -> None:
    entities = relations.normalise(
        [_entity("Loan", "Member", "Book"), _entity("Member"), _entity("Book"),
         _entity("Fine", "Loan")]
    )  # fmt: skip
    assert [e.name for e in relations.creation_order(entities)] == [
        "Member", "Book", "Loan", "Fine",
    ]  # fmt: skip
    assert [e.name for e in relations.children_of("Loan", entities)] == ["Fine"]


def test_initial_migration_creates_in_dependency_order_and_drops_in_reverse() -> None:
    entities = relations.normalise([_entity("Loan", "Member"), _entity("Member")])
    script = migrations.render_initial_migration(entities)
    upgrade, downgrade = script.split("def downgrade() -> None:")
    assert upgrade.index('"members"') < upgrade.index('"loans"')
    assert 'sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE")' in upgrade
    assert 'op.create_index("ix_loans_member_id", "loans", ["member_id"])' in upgrade
    assert downgrade.index("drop_index") < downgrade.index('drop_table("loans")')
    assert downgrade.index('drop_table("loans")') < downgrade.index('drop_table("members")')
    compile(script, "0001_initial.py", "exec")


# --- generated projects --------------------------------------------------------------------


async def _generate(stack: Stack, root: Path, spec: str = LIBRARY_SPEC) -> PipelineState:
    state = PipelineState(spec=spec, stack=stack)

    async def quiet(_: str) -> None:
        return None

    await run_pipeline(state, OfflineProvider(), quiet)
    for file in state.files.values():
        target = root / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8", newline="\n")
    return state


async def _pytest(project: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:cov", *args],
        cwd=project,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


async def test_python_project_owns_its_schema_with_alembic(tmp_path: Path) -> None:
    state = await _generate(Stack.PYTHON, tmp_path)
    files = state.files
    alembic = {"alembic.ini", "migrations/env.py", "migrations/script.py.mako",
               "migrations/versions/0001_initial.py", "tests/test_migrations.py"}  # fmt: skip
    assert alembic <= set(files)
    assert "create_all" not in files["app/main.py"].content
    assert "alembic>=" in files["pyproject.toml"].content
    dockerfile = files["Dockerfile"].content
    assert "COPY migrations ./migrations" in dockerfile
    assert "alembic upgrade head && exec uvicorn" in dockerfile
    models = files["app/models.py"].content
    assert 'ForeignKey("members.id", ondelete="CASCADE"), index=True' in models
    assert state.architecture is not None
    decisions = [d.title for d in state.architecture.decisions]
    assert "Alembic migrations own the schema" in decisions
    assert state.review is not None
    assert any("cascade" in finding.message for finding in state.review.findings)


async def test_python_project_relations_and_migrations_pass_their_own_tests(
    tmp_path: Path,
) -> None:
    await _generate(Stack.PYTHON, tmp_path)
    out = await _pytest(tmp_path, "tests/test_loans.py", "tests/test_migrations.py", "-rs")
    assert "11 passed, 1 skipped" in out, out  # 10 loan tests + SQLite migrations; no Postgres
    assert "set TEST_DATABASE_URL" in out


async def _ruff(project: Path, *args: str) -> None:
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "ruff", *args, "--no-cache", "."],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


async def test_nested_parents_generate_clean_passing_code(tmp_path: Path) -> None:
    # Ticket -> Event -> Organizer: a ticket's test creates an event, which creates an organizer.
    spec = "An events platform where organizers publish events and attendees buy tickets."
    state = await _generate(Stack.PYTHON, tmp_path, spec)
    assert "def _make_organizer(" in state.files["tests/test_tickets.py"].content
    await _ruff(tmp_path, "check")
    await _ruff(tmp_path, "format", "--check")
    out = await _pytest(tmp_path)
    assert "36 passed, 1 skipped" in out, out


@pytest.mark.skipif(POSTGRES_URL is None, reason="set AUTOSWE_TEST_POSTGRES_URL to run")
async def test_generated_migrations_upgrade_and_downgrade_on_postgres(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, text

    assert POSTGRES_URL is not None
    sync_url = POSTGRES_URL.replace("+asyncpg", "+psycopg")
    schema = f"generated_{uuid.uuid4().hex[:8]}"  # isolated from the platform's own tables
    admin = create_engine(sync_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        await _generate(Stack.PYTHON, tmp_path)
        separator = "&" if "?" in sync_url else "?"
        url = f"{sync_url}{separator}options=-csearch_path%3D{schema}"
        out = await _pytest(tmp_path, "tests/test_migrations.py", env={"TEST_DATABASE_URL": url})
        assert "2 passed" in out, out
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


async def test_typescript_and_go_projects_carry_the_relations(tmp_path: Path) -> None:
    ts = (await _generate(Stack.TYPESCRIPT, tmp_path / "ts")).files
    app = ts["src/app.ts"].content
    assert (
        'resource("Loan", loanFields, [{ key: "member_id", label: "Member", store: members.store }'
        in app
    )
    assert app.index("const members = resource(") < app.index("const loans = resource(")
    assert '{ name: "member_id", type: "integer", required: true }' in ts["src/models.ts"].content
    assert "cascadeTo(key: string, store: Store)" in ts["src/store.ts"].content
    loan_test = ts["test/loans.test.ts"].content
    assert 'it("requires an existing member"' in loan_test
    assert 'it("is deleted with its member"' in loan_test

    go = (await _generate(Stack.GO, tmp_path / "go")).files
    handlers = go["handlers.go"].content
    assert ('newResource("Loan", loanFields, reference{key: "member_id", label: "Member", '
            "store: members.store}") in handlers  # fmt: skip
    assert "func (s *Store) deleteWhere(key string, id int64)" in go["store.go"].content
    assert "func makeMember(t *testing.T" in go["members_test.go"].content
    assert "func TestLoanIsDeletedWithItsMember" in go["loans_test.go"].content


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js 24")
async def test_typescript_relations_hold_over_http(tmp_path: Path) -> None:
    await _generate(Stack.TYPESCRIPT, tmp_path)
    completed = await asyncio.to_thread(
        subprocess.run,
        ["node", "--test", "--test-reporter=tap", "test/loans.test.ts"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "# pass 9" in completed.stdout and "# fail 0" in completed.stdout, completed.stdout


# --- eval ----------------------------------------------------------------------------------


def test_relation_expectations_are_validated() -> None:
    assert Expectations(relations=["Loan -> Member"]).relation_pairs() == [("Loan", "Member")]
    with pytest.raises(ValueError, match="Child -> Parent"):
        Expectations(relations=["Loan belongs to Member"])


async def test_relation_recall_needs_the_link_and_its_key_in_the_code(tmp_path: Path) -> None:
    state = await _generate(Stack.PYTHON, tmp_path)
    fixture = EvalFixture(
        name="library",
        spec=LIBRARY_SPEC,
        expected=Expectations(relations=["Loan -> Member", "Loan -> Book", "Book -> Shelf"]),
    )
    result = score(fixture, state, None)
    assert result.metrics["relation_recall"] == pytest.approx(2 / 3)
    assert result.missing_relations == ["Book -> Shelf"]
    no_expectation = EvalFixture(name="plain", spec=LIBRARY_SPEC)
    assert score(no_expectation, state, None).metrics["relation_recall"] is None
