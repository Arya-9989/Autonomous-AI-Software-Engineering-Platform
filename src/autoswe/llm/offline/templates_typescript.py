"""Source templates for the TypeScript service the offline provider generates.

A complete, dependency-free project for Node.js 24: ``node:http`` with a small typed router, an
in-memory store, input validation driven by each entity's field list, and a ``node:test`` suite
that exercises every endpoint over real HTTP. Node runs the TypeScript directly (type stripping),
so the only development dependencies are the type checker and the linter.
"""

from __future__ import annotations

import json
from textwrap import dedent

from autoswe.pipeline import relations
from autoswe.pipeline.artifacts import ArchitectureSpec, Entity, EntityField, GeneratedFile
from autoswe.pipeline.naming import kebab, pluralize, snake

# Kept in step with sandbox/node/package.json, so the sandbox checks with these versions.
DEV_DEPENDENCIES = {
    "@eslint/js": "10.0.1",
    "@types/node": "24.19.2",
    "eslint": "10.13.0",
    "typescript": "6.0.3",
    "typescript-eslint": "8.72.0",
}

_TS_TYPES = {
    "string": "string",
    "text": "string",
    "integer": "number",
    "float": "number",
    "boolean": "boolean",
    "date": "string",
    "datetime": "string",
}
_SAMPLES = {
    "string": ('"example {name}"', '"updated {name}"'),
    "text": ('"Some longer {name} text."', '"Revised {name} text."'),
    "integer": ("3", "7"),
    "float": ("9.5", "12.25"),
    "boolean": ("true", "false"),
    "date": ('"2025-01-15"', '"2025-02-20"'),
    "datetime": ('"2025-01-15T09:30:00Z"', '"2025-02-20T14:00:00Z"'),
}


def _src(text: str) -> str:
    return dedent(text).lstrip("\n")


def _camel(value: str) -> str:
    head, *rest = snake(value).split("_")
    return head + "".join(part.capitalize() for part in rest)


def _sample(field: EntityField, *, updated: bool = False) -> str:
    return _SAMPLES[field.type][1 if updated else 0].replace("{name}", field.name.replace("_", " "))


def render_package_json(name: str, summary: str) -> str:
    package = {
        "name": name,
        "version": "0.1.0",
        "description": summary.replace("\n", " ")[:200],
        "private": True,
        "type": "module",
        "engines": {"node": ">=24"},
        "scripts": {
            "start": "node src/server.ts",
            "test": "node --test test/*.test.ts",
            "typecheck": "tsc --noEmit -p .",
            "lint": "eslint .",
        },
        "devDependencies": DEV_DEPENDENCIES,
    }
    return json.dumps(package, indent=2) + "\n"


def render_tsconfig() -> str:
    config = {
        "compilerOptions": {
            "target": "es2024",
            "module": "nodenext",
            "moduleResolution": "nodenext",
            "strict": True,
            "noEmit": True,
            "allowImportingTsExtensions": True,
            "erasableSyntaxOnly": True,
            "verbatimModuleSyntax": True,
            "noUncheckedIndexedAccess": True,
            "types": ["node"],
            "skipLibCheck": True,
        },
        "include": ["src", "test"],
    }
    return json.dumps(config, indent=2) + "\n"


def render_eslint_config() -> str:
    return _src("""
        import js from "@eslint/js";
        import tseslint from "typescript-eslint";

        export default tseslint.config(js.configs.recommended, ...tseslint.configs.recommended);
    """)


def render_validation() -> str:
    return _src("""
        /** Input validation driven by each entity's field list. */

        export type FieldType = "string" | "text" | "integer" | "float" | "boolean" | "date" | "datetime";

        export interface FieldSpec {
          readonly name: string;
          readonly type: FieldType;
          readonly required: boolean;
        }

        const DATE = /^\\d{4}-\\d{2}-\\d{2}$/;

        function accepts(type: FieldType, value: unknown): boolean {
          switch (type) {
            case "string":
              return typeof value === "string" && value.length <= 255;
            case "text":
              return typeof value === "string";
            case "integer":
              return Number.isInteger(value);
            case "float":
              return typeof value === "number" && Number.isFinite(value);
            case "boolean":
              return typeof value === "boolean";
            case "date":
              return typeof value === "string" && DATE.test(value) && !Number.isNaN(Date.parse(value));
            case "datetime":
              return typeof value === "string" && !DATE.test(value) && !Number.isNaN(Date.parse(value));
          }
        }

        export type Validated = { data: Record<string, unknown> } | { errors: string[] };

        /** Check a JSON body; `partial` (PATCH) allows any subset of the fields. */
        export function validate(fields: readonly FieldSpec[], body: unknown, partial: boolean): Validated {
          if (typeof body !== "object" || body === null || Array.isArray(body)) {
            return { errors: ["body must be a JSON object"] };
          }
          const input = body as Record<string, unknown>;
          const errors: string[] = [];
          const data: Record<string, unknown> = {};
          const known = new Set(fields.map((field) => field.name));
          for (const key of Object.keys(input)) {
            if (!known.has(key)) errors.push(`${key}: unknown field`);
          }
          for (const field of fields) {
            const value = input[field.name];
            if (value === undefined) {
              if (field.required && !partial) errors.push(`${field.name}: required`);
              continue;
            }
            if (value === null && !field.required) {
              data[field.name] = null;
            } else if (accepts(field.type, value)) {
              data[field.name] = value;
            } else {
              errors.push(`${field.name}: expected ${field.type}`);
            }
          }
          return errors.length > 0 ? { errors } : { data };
        }
    """)


def render_store() -> str:
    return _src("""
        /** In-memory storage for one entity. Replace it with a database before scaling out. */

        export interface Stored {
          id: number;
          created_at: string;
          [field: string]: unknown;
        }

        export class Store {
          readonly #items = new Map<number, Stored>();
          /** Stores whose items point at this one's (by `key`): deleting here deletes there. */
          readonly #dependents: { readonly key: string; readonly store: Store }[] = [];
          #nextId = 1;

          /** Items whose `where` keys all equal the given ids, a window of them. */
          list(limit: number, offset: number, where: Readonly<Record<string, number>> = {}): Stored[] {
            const conditions = Object.entries(where);
            return [...this.#items.values()]
              .filter((item) => conditions.every(([key, value]) => item[key] === value))
              .slice(offset, offset + limit);
          }

          /** Register `store`'s items as children of this store's items through their `key`. */
          cascadeTo(key: string, store: Store): void {
            this.#dependents.push({ key, store });
          }

          get(id: number): Stored | undefined {
            return this.#items.get(id);
          }

          create(data: Record<string, unknown>): Stored {
            const item: Stored = { ...data, id: this.#nextId++, created_at: new Date().toISOString() };
            this.#items.set(item.id, item);
            return item;
          }

          update(id: number, data: Record<string, unknown>): Stored | undefined {
            const current = this.#items.get(id);
            if (current === undefined) return undefined;
            const updated: Stored = { ...current, ...data, id: current.id, created_at: current.created_at };
            this.#items.set(id, updated);
            return updated;
          }

          delete(id: number): boolean {
            if (!this.#items.delete(id)) return false;
            for (const { key, store } of this.#dependents) {
              for (const item of [...store.#items.values()]) {
                if (item[key] === id) store.delete(item.id);
              }
            }
            return true;
          }
        }
    """)


def _references(entity: Entity) -> list[tuple[str, str]]:
    """(parent, foreign key) pairs; ``entity.belongs_to`` is already normalised."""
    return [(parent, relations.foreign_key(parent)) for parent in entity.belongs_to]


def render_models(entities: list[Entity]) -> str:
    lines = [
        "/** Entity models: the field lists drive validation; the interfaces document the JSON. */",
        "",
        'import type { FieldSpec } from "./validation.ts";',
    ]
    for entity in entities:
        lines += ["", f"export interface {entity.name} {{", "  id: number;"]
        for field in entity.fields:
            ts_type = _TS_TYPES[field.type] + ("" if field.required else " | null")
            lines.append(f"  {field.name}: {ts_type};")
        lines += [f"  {key}: number;" for _, key in _references(entity)]
        lines += ["  created_at: string;", "}", ""]
        lines.append(f"export const {_camel(entity.name)}Fields: readonly FieldSpec[] = [")
        for field in entity.fields:
            required = "true" if field.required else "false"
            lines.append(
                f'  {{ name: "{field.name}", type: "{field.type}", required: {required} }},'
            )
        lines += [
            f'  {{ name: "{key}", type: "integer", required: true }},'
            for _, key in _references(entity)
        ]
        lines.append("];")
    return "\n".join(lines) + "\n"


def render_app(entities: list[Entity]) -> str:
    imports = ", ".join(sorted(f"{_camel(e.name)}Fields" for e in entities))
    lines = []
    for entity in relations.creation_order(entities):  # a parent's store exists before its children
        var = _camel(pluralize(entity.name))
        refs = [
            f'{{ key: "{key}", label: "{parent}", store: {_camel(pluralize(parent))}.store }}'
            for parent, key in _references(entity)
        ]
        args = f'"{entity.name}", {_camel(entity.name)}Fields' + (
            ", [" + ", ".join(refs) + "]" if refs else ""
        )
        lines.append(f"  const {var} = resource({args});")
    routes = ['    { method: "GET", path: "/healthz", handle: ({ response }) => send(response, 200, '
              '{ status: "ok" }) },']  # fmt: skip
    for entity in entities:
        var = _camel(pluralize(entity.name))
        base = "/" + kebab(pluralize(entity.name))
        routes += [
            f'    {{ method: "GET", path: "{base}", handle: {var}.list }},',
            f'    {{ method: "POST", path: "{base}", handle: {var}.create }},',
            f'    {{ method: "GET", path: "{base}/{{id}}", handle: {var}.get }},',
            f'    {{ method: "PATCH", path: "{base}/{{id}}", handle: {var}.update }},',
            f'    {{ method: "DELETE", path: "{base}/{{id}}", handle: {var}.remove }},',
        ]
    return (
        _src(f"""
        /** The HTTP API: a route table with CRUD handlers for every entity, plus GET /healthz. */

        import type {{ IncomingMessage, ServerResponse }} from "node:http";

        import {{ {imports} }} from "./models.ts";
        import {{ Store }} from "./store.ts";
        import {{ type FieldSpec, validate }} from "./validation.ts";
        """)
        + "\n"
        + _src("""
        const MAX_BODY_BYTES = 1_000_000;

        export type Handler = (request: IncomingMessage, response: ServerResponse) => Promise<void>;

        interface Context {
          readonly request: IncomingMessage;
          readonly response: ServerResponse;
          readonly url: URL;
          readonly params: Readonly<Record<string, string>>;
        }

        interface Route {
          readonly method: string;
          readonly path: string;
          readonly handle: (context: Context) => void | Promise<void>;
        }

        function send(response: ServerResponse, status: number, body?: unknown): void {
          if (body === undefined) {
            response.writeHead(status).end();
            return;
          }
          response.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(body));
        }

        class BodyError extends Error {
          readonly status: number;

          constructor(status: number, message: string) {
            super(message);
            this.status = status;
          }
        }

        async function readJson(request: IncomingMessage): Promise<unknown> {
          let size = 0;
          const chunks: Buffer[] = [];
          for await (const chunk of request) {
            const buffer = chunk as Buffer;
            size += buffer.length;
            if (size > MAX_BODY_BYTES) throw new BodyError(413, "Body too large");
            chunks.push(buffer);
          }
          try {
            return JSON.parse(Buffer.concat(chunks).toString("utf8"));
          } catch {
            throw new BodyError(422, "Invalid JSON");
          }
        }

        /** Matches "/books/{id}" against "/books/7" and returns { id: "7" }, or undefined. */
        function match(pattern: string, pathname: string): Record<string, string> | undefined {
          const want = pattern.split("/").filter(Boolean);
          const got = pathname.split("/").filter(Boolean);
          if (want.length !== got.length) return undefined;
          const params: Record<string, string> = {};
          for (const [index, part] of want.entries()) {
            const value = got[index] ?? "";
            if (part.startsWith("{") && part.endsWith("}")) params[part.slice(1, -1)] = decodeURIComponent(value);
            else if (part !== value) return undefined;
          }
          return params;
        }

        function router(routes: readonly Route[]): Handler {
          return async (request, response) => {
            const url = new URL(request.url ?? "/", "http://localhost");
            const method = request.method ?? "GET";
            let pathMatched = false;
            for (const route of routes) {
              const params = match(route.path, url.pathname);
              if (params === undefined) continue;
              pathMatched = true;
              if (route.method !== method) continue;
              try {
                await route.handle({ request, response, url, params });
              } catch (error) {
                if (!(error instanceof BodyError)) throw error;
                send(response, error.status, { detail: error.message });
              }
              return;
            }
            send(response, pathMatched ? 405 : 404, { detail: pathMatched ? "Method not allowed" : "Not found" });
          };
        }

        function page(url: URL): { limit: number; offset: number } | undefined {
          const limit = Number(url.searchParams.get("limit") ?? "50");
          const offset = Number(url.searchParams.get("offset") ?? "0");
          if (!Number.isInteger(limit) || limit < 1 || limit > 200) return undefined;
          if (!Number.isInteger(offset) || offset < 0) return undefined;
          return { limit, offset };
        }

        function positive(raw: string): number | undefined {
          const id = Number(raw);
          return /^[1-9][0-9]*$/.test(raw) && Number.isSafeInteger(id) ? id : undefined;
        }

        function idOf(params: Readonly<Record<string, string>>): number | undefined {
          return positive(params.id ?? "");
        }

        /** A many-to-one link: `key` holds the id of an item in the parent's `store`. */
        interface Reference {
          readonly key: string;
          readonly label: string;
          readonly store: Store;
        }

        /** CRUD handlers for one entity, over its own in-memory store. */
        function resource(label: string, fields: readonly FieldSpec[], references: readonly Reference[] = []) {
          const store = new Store();
          for (const reference of references) reference.store.cascadeTo(reference.key, store);
          const notFound = { detail: `${label} not found` };
          /** The first referenced parent that does not exist, as an error message. */
          const missingParent = (data: Record<string, unknown>): string | undefined => {
            for (const { key, label: parent, store: parents } of references) {
              const value = data[key];
              if (typeof value === "number" && parents.get(value) === undefined) return `${parent} ${value} not found`;
            }
            return undefined;
          };
          return {
            store,
            list: ({ response, url }: Context) => {
              const window = page(url);
              const where: Record<string, number> = {};
              for (const { key } of references) {
                const raw = url.searchParams.get(key);
                if (raw === null) continue;
                const id = positive(raw);
                if (id === undefined) {
                  send(response, 422, { detail: [`${key}: expected a positive integer`] });
                  return;
                }
                where[key] = id;
              }
              if (window === undefined) send(response, 422, { detail: ["limit or offset out of range"] });
              else send(response, 200, store.list(window.limit, window.offset, where));
            },
            create: async ({ request, response }: Context) => {
              const checked = validate(fields, await readJson(request), false);
              if ("errors" in checked) {
                send(response, 422, { detail: checked.errors });
                return;
              }
              const missing = missingParent(checked.data);
              if (missing !== undefined) send(response, 422, { detail: missing });
              else send(response, 201, store.create(checked.data));
            },
            get: ({ response, params }: Context) => {
              const id = idOf(params);
              const item = id === undefined ? undefined : store.get(id);
              if (item === undefined) send(response, 404, notFound);
              else send(response, 200, item);
            },
            update: async ({ request, response, params }: Context) => {
              const id = idOf(params);
              if (id === undefined) {
                send(response, 404, notFound);
                return;
              }
              const checked = validate(fields, await readJson(request), true);
              if ("errors" in checked) {
                send(response, 422, { detail: checked.errors });
                return;
              }
              if (store.get(id) === undefined) {
                send(response, 404, notFound);
                return;
              }
              const missing = missingParent(checked.data);
              if (missing !== undefined) {
                send(response, 422, { detail: missing });
                return;
              }
              const item = store.update(id, checked.data);
              if (item === undefined) send(response, 404, notFound);
              else send(response, 200, item);
            },
            remove: ({ response, params }: Context) => {
              const id = idOf(params);
              if (id !== undefined && store.delete(id)) send(response, 204);
              else send(response, 404, notFound);
            },
          };
        }
        """)
        + "\nexport function createApp(): Handler {\n"
        + "\n".join(lines)
        + "\n  return router([\n"
        + "\n".join(routes)
        + "\n  ]);\n}\n"
    )


def render_server() -> str:
    return _src("""
        import { createServer } from "node:http";

        import { createApp } from "./app.ts";

        const port = Number(process.env.PORT ?? "8000");
        createServer(createApp()).listen(port, () => {
          console.log(`listening on :${port}`);
        });
    """)


def render_test_server() -> str:
    return _src("""
        /** Starts the app on an ephemeral port for one test file; each file gets a fresh store. */

        import { createServer, type Server } from "node:http";
        import type { AddressInfo } from "node:net";

        import { createApp } from "../src/app.ts";

        export interface Running {
          readonly url: string;
          close(): Promise<void>;
        }

        export async function start(): Promise<Running> {
          const server: Server = createServer(createApp());
          await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
          const { port } = server.address() as AddressInfo;
          return {
            url: `http://127.0.0.1:${port}`,
            close: () => new Promise<void>((resolve) => server.close(() => resolve())),
          };
        }

        export async function json(response: Response): Promise<Record<string, unknown>> {
          return (await response.json()) as Record<string, unknown>;
        }

        export function post(url: string, body: unknown): Promise<Response> {
          return fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
        }

        export function patch(url: string, body: unknown): Promise<Response> {
          return fetch(url, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
        }
    """)


def _ancestors(entity: Entity, by_name: dict[str, Entity]) -> list[Entity]:
    """Every entity ``entity`` needs created first, parents of parents before their children."""
    ordered: list[Entity] = []

    def visit(name: str) -> None:
        for parent in by_name[name].belongs_to:
            if by_name[parent] not in ordered:
                visit(parent)
                ordered.append(by_name[parent])

    visit(entity.name)
    return ordered


def _body(constant: str, entity: Entity) -> str:
    """A request body: the sample payload plus a freshly created parent for each reference."""
    if not entity.belongs_to:
        return constant
    keys = ", ".join(f"{key}: await {_maker_name(parent)}()" for parent, key in _references(entity))
    return f"{{ ...{constant}, {keys} }}"


def _maker_name(parent: str) -> str:
    return "make" + parent


def render_entity_test(entity: Entity, entities: list[Entity] | None = None) -> str:
    by_name = {e.name: e for e in (entities or [entity])}
    prefix = "/" + kebab(pluralize(entity.name))
    comparable = [f for f in entity.fields if f.type not in {"date", "datetime"}]
    target = next((f for f in entity.fields if f.type in {"string", "text"}), entity.fields[0])
    payload = ", ".join(f"{f.name}: {_sample(f)}" for f in entity.fields)
    ancestors = _ancestors(entity, by_name)
    body = "await payload()" if entity.belongs_to else "PAYLOAD"
    lines = [
        'import assert from "node:assert/strict";',
        'import { after, before, describe, it } from "node:test";',
        "",
        'import { json, patch, post, type Running, start } from "./server.ts";',
        "",
        f"const PAYLOAD = {{ {payload} }};",
    ]
    for ancestor in ancestors:
        sample = ", ".join(f"{f.name}: {_sample(f)}" for f in ancestor.fields)
        lines.append(f"const {snake(ancestor.name).upper()} = {{ {sample} }};")
    lines += [
        "",
        f'describe("{prefix}", () => {{',
        "  let app: Running;",
        "  before(async () => {",
        "    app = await start();",
        "  });",
        "  after(() => app.close());",
    ]
    for ancestor in ancestors:
        path = "/" + kebab(pluralize(ancestor.name))
        lines += [
            "",
            f"  async function {_maker_name(ancestor.name)}(): Promise<number> {{",
            f"    const response = await post(`${{app.url}}{path}`, "
            f"{_body(snake(ancestor.name).upper(), ancestor)});",
            "    assert.equal(response.status, 201);",
            "    return (await json(response)).id as number;",
            "  }",
        ]
    if entity.belongs_to:
        lines += [
            "",
            "  async function payload(): Promise<Record<string, unknown>> {",
            f"    return {_body('PAYLOAD', entity)};",
            "  }",
        ]
    lines += [
        "",
        '  it("creates and reads one", async () => {',
        *(["    const body = await payload();"] if entity.belongs_to else []),
        f"    const response = await post(`${{app.url}}{prefix}`, "
        f"{'body' if entity.belongs_to else 'PAYLOAD'});",
        "    assert.equal(response.status, 201);",
        "    const created = await json(response);",
        *[f"    assert.equal(created.{f.name}, PAYLOAD.{f.name});" for f in comparable],
        *[f"    assert.equal(created.{key}, body.{key});" for _, key in _references(entity)],
        f"    const fetched = await fetch(`${{app.url}}{prefix}/${{created.id}}`);",
        "    assert.equal(fetched.status, 200);",
        "    assert.equal((await json(fetched)).id, created.id);",
        "  });",
        "",
        '  it("lists with limit and offset", async () => {',
        "    for (let i = 0; i < 3; i++) {",
        f"      assert.equal((await post(`${{app.url}}{prefix}`, {body})).status, 201);",
        "    }",
        f"    const page = await fetch(`${{app.url}}{prefix}?limit=2&offset=1`);",
        "    assert.equal(page.status, 200);",
        "    assert.equal(((await page.json()) as unknown[]).length, 2);",
        f"    assert.equal((await fetch(`${{app.url}}{prefix}?limit=0`)).status, 422);",
        "  });",
        "",
        '  it("updates one", async () => {',
        f"    const created = await json(await post(`${{app.url}}{prefix}`, {body}));",
        f"    const response = await patch(`${{app.url}}{prefix}/${{created.id}}`, "
        f"{{ {target.name}: {_sample(target, updated=True)} }});",
        "    assert.equal(response.status, 200);",
        f"    assert.equal((await json(response)).{target.name}, {_sample(target, updated=True)});",
        "  });",
        "",
        '  it("deletes one", async () => {',
        f"    const created = await json(await post(`${{app.url}}{prefix}`, {body}));",
        f'    const deleted = await fetch(`${{app.url}}{prefix}/${{created.id}}`, {{ method: "DELETE" }});',
        "    assert.equal(deleted.status, 204);",
        f"    assert.equal((await fetch(`${{app.url}}{prefix}/${{created.id}}`)).status, 404);",
        "  });",
        "",
        '  it("answers 404 for an unknown id", async () => {',
        f"    assert.equal((await fetch(`${{app.url}}{prefix}/999999`)).status, 404);",
        f"    assert.equal((await fetch(`${{app.url}}{prefix}/not-a-number`)).status, 404);",
        "  });",
        "",
        '  it("rejects invalid input with 422", async () => {',
        f"    assert.equal((await post(`${{app.url}}{prefix}`, {{ unknown_field: 1 }})).status, 422);",
    ]
    if any(f.required for f in entity.fields) or entity.belongs_to:
        lines.append(f"    assert.equal((await post(`${{app.url}}{prefix}`, {{}})).status, 422);")
    lines.append("  });")
    if entity.belongs_to:
        parent, key = _references(entity)[0]
        parent_path = "/" + kebab(pluralize(parent))
        lines += [
            "",
            f'  it("requires an existing {parent.lower()}", async () => {{',
            f"    const response = await post(`${{app.url}}{prefix}`, "
            f"{{ ...(await payload()), {key}: 999999 }});",
            "    assert.equal(response.status, 422);",
            f'    assert.equal((await json(response)).detail, "{parent} 999999 not found");',
            "  });",
            "",
            f'  it("filters by {parent.lower()}", async () => {{',
            "    const mine = await payload();",
            f"    assert.equal((await post(`${{app.url}}{prefix}`, mine)).status, 201);",
            f"    assert.equal((await post(`${{app.url}}{prefix}`, await payload())).status, 201);",
            f"    const page = await fetch(`${{app.url}}{prefix}?{key}=${{String(mine.{key})}}`);",
            "    const rows = (await page.json()) as Record<string, unknown>[];",
            f"    assert.deepEqual(rows.map((row) => row.{key}), [mine.{key}]);",
            f"    assert.equal((await fetch(`${{app.url}}{prefix}?{key}=0`)).status, 422);",
            "  });",
            "",
            f'  it("is deleted with its {parent.lower()}", async () => {{',
            f"    const created = await json(await post(`${{app.url}}{prefix}`, await payload()));",
            f"    const parent = `${{app.url}}{parent_path}/${{String(created.{key})}}`;",
            '    assert.equal((await fetch(parent, { method: "DELETE" })).status, 204);',
            f"    assert.equal((await fetch(`${{app.url}}{prefix}/${{String(created.id)}}`)).status, 404);",
            "  });",
        ]
    lines.append("});")
    return "\n".join(lines) + "\n"


def render_health_test() -> str:
    return _src("""
        import assert from "node:assert/strict";
        import { after, before, it } from "node:test";

        import { json, type Running, start } from "./server.ts";

        let app: Running;
        before(async () => {
          app = await start();
        });
        after(() => app.close());

        it("reports healthy", async () => {
          const response = await fetch(`${app.url}/healthz`);
          assert.equal(response.status, 200);
          assert.deepEqual(await json(response), { status: "ok" });
        });
    """)


def render_readme(name: str, summary: str, architecture: ArchitectureSpec) -> str:
    rows = "\n".join(f"| `{e.method}` | `{e.path}` | {e.summary} |" for e in architecture.endpoints)
    return (
        _src(f"""
        # {name}

        {summary}

        > Generated by the Autonomous AI Software Engineering Platform (offline provider).
        > Review the code before deploying it anywhere that matters.

        TypeScript on Node.js 24 with no runtime dependencies. Data is kept in memory, so run one
        replica until a database replaces `src/store.ts`. A record that belongs to another (a
        `<parent>_id` field) must point at one that exists, lists can be filtered by it, and
        deleting the parent deletes its children.

        ## Run and test

        ```bash
        npm install          # type checker and linter only
        npm start            # http://localhost:8000
        npm run lint && npm run typecheck && npm test
        ```

        ## API

        | Method | Path | Description |
        | --- | --- | --- |
        """)
        + rows
        + "\n"
    )


def render_project(
    project: str, summary: str, architecture: ArchitectureSpec
) -> list[GeneratedFile]:
    entities = relations.normalise(architecture.entities)
    files = [
        GeneratedFile(path="package.json", content=render_package_json(project, summary),
                      description="Package metadata, scripts and development dependencies"),
        GeneratedFile(path="tsconfig.json", content=render_tsconfig(),
                      description="Strict type checking; Node runs the sources directly"),
        GeneratedFile(path="eslint.config.js", content=render_eslint_config(),
                      description="ESLint with typescript-eslint's recommended rules"),
        GeneratedFile(path="README.md", content=render_readme(project, summary, architecture),
                      description="How to run, test and deploy the service"),
        GeneratedFile(path="src/validation.ts", content=render_validation(),
                      description="Field-driven input validation"),
        GeneratedFile(path="src/store.ts", content=render_store(),
                      description="In-memory storage per entity"),
        GeneratedFile(path="src/models.ts", content=render_models(entities),
                      description="Entity interfaces and field lists"),
        GeneratedFile(path="src/app.ts", content=render_app(entities),
                      description="HTTP routing and CRUD handlers"),
        GeneratedFile(path="src/server.ts", content=render_server(),
                      description="Process entry point"),
        GeneratedFile(path="test/server.ts", content=render_test_server(),
                      description="Test helpers: an app on an ephemeral port"),
        GeneratedFile(path="test/health.test.ts", content=render_health_test(),
                      description="Health endpoint test"),
    ]  # fmt: skip
    for entity in entities:
        module = kebab(pluralize(entity.name))
        files.append(
            GeneratedFile(
                path=f"test/{module}.test.ts",
                content=render_entity_test(entity, entities),
                description=f"API tests for {pluralize(entity.name).lower()}",
            )
        )
    return files
