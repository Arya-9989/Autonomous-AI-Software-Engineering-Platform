# ADR 0008: Entity relationships and Alembic migrations in generated projects

- Status: accepted
- Date: 2026-10-23

## Context

Generated projects modelled every entity as a standalone table. The library spec reads "members
borrow books and librarians track loans", yet a Loan had no link to its Member or Book: nothing
stopped a loan for a book that does not exist, and deleting a member left orphaned loans. The
Python project also created its schema with `metadata.create_all()` at startup. That can't
evolve a schema, isn't reviewable, and isn't reversible, and the architect's own decision
record listed "adopt Alembic before schemas evolve" as a follow-up.

## Options considered

- **Relationships as free-form fields** (`member_id: integer` written by the analyst): no
  referential check, no index, nothing for the code generator to act on.
- **Many-to-one links on the entity (chosen)**: `Entity.belongs_to: list[str]`. It covers the
  common case (a Loan belongs to a Member and a Book). Many-to-many can be modelled as an explicit
  join entity that belongs to both sides, so it needs no second mechanism.
- **Schema at startup vs. migrations**: `create_all()` keeps the scaffold short. Alembic adds four
  files, but they are the files a real service has, and a test can check them.
- **Validating migrations against PostgreSQL inside the sandbox**: the sandbox has no network
  (ADR 0001), so it can't reach a database service, and an in-image PostgreSQL would add
  ~200 MB and a server process to every run. The sandbox validates on SQLite. PostgreSQL is
  checked by the platform's CI and by the generated project itself whenever `TEST_DATABASE_URL`
  is set.

## Decision

- **Contract.** `Entity.belongs_to` names parent entities. Each one becomes a required
  `<parent>_id` key (`autoswe.pipeline.relations.foreign_key`). Before rendering, the renderers
  normalise the links (`relations.normalise`): they drop unknown parents, self-links, duplicates,
  and any edge that would close a cycle, so a live model's output always has a creation order.
- **Analysis.** Explicit specs declare links with an entity-typed field, as in
  `Comment(post:Post, author)`; the field becomes the link, not a column. Prose specs get links
  from a small table of common domain links (loan → member/book, ticket → event/attendee,
  pet → owner, ...), applied only when both entities were found. Explicit specs get no guessed
  links.
- **Python project.**
  - `ForeignKey(..., ondelete="CASCADE")` with an index on every key, and SQLite foreign-key
    enforcement switched on (`PRAGMA foreign_keys=ON`).
  - Pydantic requires `<parent>_id > 0`, and the router answers 422 `"<Parent> <id> not found"`
    before inserting.
  - Lists filter by each key (`GET /loans?member_id=3`).
  - Alembic owns the schema: `alembic.ini`, `migrations/env.py` (bound to `app.models` and
    `DATABASE_URL`; batch mode on SQLite; `compare_type`), and an initial revision that creates
    tables parents-first and drops them children-first.
  - The app never calls `create_all()`. The test suite builds its database with
    `alembic upgrade head`, and `tests/test_migrations.py` checks that the migrations upgrade,
    match the models (`compare_metadata` drift is empty), downgrade to nothing and upgrade again.
    It runs on SQLite always, and on PostgreSQL when `TEST_DATABASE_URL` is set.
  - The container runs `alembic upgrade head && exec uvicorn ...`. `env.py` takes a PostgreSQL
    advisory lock and commits it before migrating, so replicas starting together apply each
    revision once.
- **TypeScript and Go projects** (in-memory, ADR 0007) get the same API contract: the key is a
  required integer field, an unknown parent gives the same 422, lists filter by key, and deleting
  a parent deletes its children. The cascade lives in the store: a parent store knows its
  dependent stores, and Go's store releases its own lock before it cascades. Resources are
  built parents-first.
- **Tests per child entity**, in every stack: an unknown parent is rejected, lists filter by
  parent, and deleting the parent deletes the child. That is on top of the CRUD tests, which now
  create real parents first.
- **No nulls in NOT NULL columns.** A `PATCH` that sets a required field or a key to `null`
  is a 422. Before this change it passed the Update schema and failed in the database as a 500,
  for required fields too. TypeScript and Go already rejected it.
- **Review and blueprint.** The architect's decision record now says "Alembic migrations own the
  schema". The reviewer's info finding about `create_all()` is gone, replaced by one that names
  every cascading link and suggests RESTRICT or soft delete where children must outlive parents.
- **Evals.** Fixtures take `relations: ["Loan -> Member", ...]`, written from the spec. The new
  `relation_recall` metric counts a link only when the requirements declare it and the generated
  code carries its `<parent>_id`. Its threshold is 0.88: measured 0.889, and the only miss is
  the clinic fixture, which also misses the Appointment and Vet entities themselves.
  `test_pass_rate` now divides by the tests that ran, so a skip (the PostgreSQL check without a
  database) is neither a pass nor a failure, and skips don't count towards `min_tests`.

## Consequences

- Generated Python projects grew from 32 to 37 files on the library spec. The sandbox image adds
  `alembic` (with `mako` and `markupsafe`) to its hash-locked requirements.
- Verified for this ADR:
  - The three stacks' library projects pass in the Docker sandbox with lint and types clean:
    Python 33 passed and 1 skipped (the PostgreSQL case), TypeScript 28/28, Go 22/22.
  - The generated migrations pass on PostgreSQL 17: upgrade, no drift, downgrade.
  - Three concurrent `alembic upgrade head` runs applied revision 0001 exactly once.
  - Deleting a member removed its loans (`ON DELETE CASCADE`).
  - An insert with an unknown `member_id` was rejected by the foreign key.
- Cascade is the generated default, so a delete can remove more than the user expects. The
  reviewer flags it on every project with links; changing a link to RESTRICT is a one-line
  model and migration change.
- The in-memory stores check a parent's existence and then insert, so two concurrent requests
  could still create a child of a parent deleted in between. Only a database closes that gap,
  which is the same seam ADR 0007 leaves open.
- Many-to-many and self-references (an Employee's manager) are not generated. Model them as a
  join entity; a self-reference is dropped by normalisation.
