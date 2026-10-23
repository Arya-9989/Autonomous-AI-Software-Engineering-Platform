"""Source templates for the Go service the offline provider generates.

A complete project that uses only the standard library: ``net/http`` with Go 1.22+ method and
path patterns, an in-memory store guarded by a mutex, field-driven validation, a
``-healthcheck`` flag for container probes, and ``httptest`` tests for every endpoint. The output
is gofmt-clean (struct columns are aligned the way gofmt aligns them).
"""

from __future__ import annotations

from collections.abc import Sequence
from textwrap import dedent

from autoswe.pipeline import relations
from autoswe.pipeline.artifacts import ArchitectureSpec, Entity, EntityField, GeneratedFile
from autoswe.pipeline.naming import kebab, pascal, pluralize, snake

GO_VERSION = "1.27"

_GO_TYPES = {
    "string": "string",
    "text": "string",
    "integer": "int64",
    "float": "float64",
    "boolean": "bool",
    "date": "string",
    "datetime": "string",
}
_FIELD_TYPES = {
    "string": "TypeString",
    "text": "TypeText",
    "integer": "TypeInteger",
    "float": "TypeFloat",
    "boolean": "TypeBoolean",
    "date": "TypeDate",
    "datetime": "TypeDateTime",
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
_INITIALISMS = {"id": "ID", "url": "URL", "isbn": "ISBN", "api": "API", "http": "HTTP"}


def _src(text: str) -> str:
    return dedent(text).lstrip("\n")


def _go_name(name: str) -> str:
    """snake_case -> exported Go identifier, with Go's usual initialisms (ID, URL, ...)."""
    return "".join(_INITIALISMS.get(part, part.capitalize()) for part in snake(name).split("_"))


def _lower(name: str) -> str:
    exported = pascal(name)
    return exported[:1].lower() + exported[1:]


def _sample(field: EntityField, *, updated: bool = False) -> str:
    return _SAMPLES[field.type][1 if updated else 0].replace("{name}", field.name.replace("_", " "))


def _aligned(rows: Sequence[tuple[str, ...]], indent: str = "\t") -> list[str]:
    """Columns padded with spaces, as gofmt's tabwriter does for consecutive lines."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    lines = []
    for row in rows:
        cells = [cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])] + [row[-1]]
        lines.append(indent + " ".join(cells).rstrip())
    return lines


def render_go_mod(name: str) -> str:
    return f"module {name}\n\ngo {GO_VERSION}\n"


def render_validation() -> str:
    consts = _aligned([
        ("TypeString", "FieldType", '= "string"'), ("TypeText", "FieldType", '= "text"'),
        ("TypeInteger", "FieldType", '= "integer"'), ("TypeFloat", "FieldType", '= "float"'),
        ("TypeBoolean", "FieldType", '= "boolean"'), ("TypeDate", "FieldType", '= "date"'),
        ("TypeDateTime", "FieldType", '= "datetime"'),
    ])  # fmt: skip
    spec = _aligned([("Name", "string"), ("Type", "FieldType"), ("Required", "bool")])
    return (
        _src("""
        package main

        import (
        \t"fmt"
        \t"math"
        \t"sort"
        \t"time"
        )

        // FieldType is the JSON type a field accepts.
        type FieldType string

        // The field types an entity can use.
        const (
        """)
        + "\n".join(consts)
        + "\n)\n\n// FieldSpec describes one field of an entity.\ntype FieldSpec struct {\n"
        + "\n".join(spec)
        + "\n}\n\n"
        + _src("""
        func accepts(kind FieldType, value any) bool {
        \tswitch kind {
        \tcase TypeString:
        \t\ttext, ok := value.(string)
        \t\treturn ok && len(text) <= 255
        \tcase TypeText:
        \t\t_, ok := value.(string)
        \t\treturn ok
        \tcase TypeInteger:
        \t\tnumber, ok := value.(float64)
        \t\treturn ok && number == math.Trunc(number) && math.Abs(number) < 1<<53
        \tcase TypeFloat:
        \t\tnumber, ok := value.(float64)
        \t\treturn ok && !math.IsInf(number, 0) && !math.IsNaN(number)
        \tcase TypeBoolean:
        \t\t_, ok := value.(bool)
        \t\treturn ok
        \tcase TypeDate:
        \t\ttext, ok := value.(string)
        \t\tif !ok {
        \t\t\treturn false
        \t\t}
        \t\t_, err := time.Parse(time.DateOnly, text)
        \t\treturn err == nil
        \tcase TypeDateTime:
        \t\ttext, ok := value.(string)
        \t\tif !ok {
        \t\t\treturn false
        \t\t}
        \t\t_, err := time.Parse(time.RFC3339, text)
        \t\treturn err == nil
        \t}
        \treturn false
        }

        // validate checks a decoded JSON object; partial (PATCH) allows any subset of fields.
        func validate(fields []FieldSpec, body map[string]any, partial bool) (map[string]any, []string) {
        \tknown := make(map[string]FieldSpec, len(fields))
        \tfor _, field := range fields {
        \t\tknown[field.Name] = field
        \t}
        \tvar problems []string
        \tfor key := range body {
        \t\tif _, ok := known[key]; !ok {
        \t\t\tproblems = append(problems, key+": unknown field")
        \t\t}
        \t}
        \tdata := make(map[string]any, len(fields))
        \tfor _, field := range fields {
        \t\tvalue, present := body[field.Name]
        \t\tswitch {
        \t\tcase !present:
        \t\t\tif field.Required && !partial {
        \t\t\t\tproblems = append(problems, field.Name+": required")
        \t\t\t}
        \t\tcase value == nil && !field.Required:
        \t\t\tdata[field.Name] = nil
        \t\tcase accepts(field.Type, value):
        \t\t\tdata[field.Name] = value
        \t\tdefault:
        \t\t\tproblems = append(problems, fmt.Sprintf("%s: expected %s", field.Name, field.Type))
        \t\t}
        \t}
        \tsort.Strings(problems)
        \treturn data, problems
        }
        """)
    )


def render_store() -> str:
    store = _aligned([("mu", "sync.Mutex"), ("items", "map[int64]Item"), ("next", "int64"),
                      ("dependents", "[]dependent")])  # fmt: skip
    dependent = _aligned([("key", "string"), ("store", "*Store")])
    return (
        _src("""
        package main

        import (
        \t"sort"
        \t"sync"
        \t"time"
        )

        // Item is one stored entity as a JSON object.
        type Item map[string]any

        // Store keeps one entity's items in memory. Replace it with a database before scaling out.
        type Store struct {
        """)
        + "\n".join(store)
        + "\n}\n\n// dependent is a store whose items point at this one's through key.\ntype dependent struct {\n"
        + "\n".join(dependent)
        + "\n}\n\n"
        + _src("""
        // NewStore returns an empty store.
        func NewStore() *Store {
        \treturn &Store{items: make(map[int64]Item), next: 1}
        }

        // CascadeTo makes deleting an item here delete the child items whose key holds its id.
        func (s *Store) CascadeTo(key string, child *Store) {
        \ts.mu.Lock()
        \tdefer s.mu.Unlock()
        \ts.dependents = append(s.dependents, dependent{key: key, store: child})
        }

        // refersTo reports whether a decoded JSON value is the given id.
        func refersTo(value any, id int64) bool {
        \tnumber, ok := value.(float64)
        \treturn ok && number == float64(id)
        }

        func matches(item Item, where map[string]int64) bool {
        \tfor key, id := range where {
        \t\tif !refersTo(item[key], id) {
        \t\t\treturn false
        \t\t}
        \t}
        \treturn true
        }

        // List returns up to limit items whose where keys match, oldest first, after skipping offset.
        func (s *Store) List(limit, offset int, where map[string]int64) []Item {
        \ts.mu.Lock()
        \tdefer s.mu.Unlock()
        \tids := make([]int64, 0, len(s.items))
        \tfor id := range s.items {
        \t\tids = append(ids, id)
        \t}
        \tsort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
        \tpage := make([]Item, 0, limit)
        \tskipped := 0
        \tfor _, id := range ids {
        \t\titem := s.items[id]
        \t\tswitch {
        \t\tcase len(page) == limit:
        \t\t\treturn page
        \t\tcase !matches(item, where):
        \t\tcase skipped < offset:
        \t\t\tskipped++
        \t\tdefault:
        \t\t\tpage = append(page, item)
        \t\t}
        \t}
        \treturn page
        }

        // Get returns the item with the given id.
        func (s *Store) Get(id int64) (Item, bool) {
        \ts.mu.Lock()
        \tdefer s.mu.Unlock()
        \titem, ok := s.items[id]
        \treturn item, ok
        }

        // Create stores a new item and returns it with its id and creation time.
        func (s *Store) Create(data map[string]any) Item {
        \ts.mu.Lock()
        \tdefer s.mu.Unlock()
        \titem := Item{"id": s.next, "created_at": time.Now().UTC().Format(time.RFC3339)}
        \tfor key, value := range data {
        \t\titem[key] = value
        \t}
        \ts.items[s.next] = item
        \ts.next++
        \treturn item
        }

        // Update merges data into an existing item.
        func (s *Store) Update(id int64, data map[string]any) (Item, bool) {
        \ts.mu.Lock()
        \tdefer s.mu.Unlock()
        \tcurrent, ok := s.items[id]
        \tif !ok {
        \t\treturn nil, false
        \t}
        \tupdated := make(Item, len(current))
        \tfor key, value := range current {
        \t\tupdated[key] = value
        \t}
        \tfor key, value := range data {
        \t\tupdated[key] = value
        \t}
        \ts.items[id] = updated
        \treturn updated, true
        }

        // Delete removes an item, and the items that belong to it, and reports whether it existed.
        func (s *Store) Delete(id int64) bool {
        \ts.mu.Lock()
        \t_, ok := s.items[id]
        \tdelete(s.items, id)
        \tdependents := s.dependents
        \ts.mu.Unlock() // not held while the children go: each store locks only itself
        \tif ok {
        \t\tfor _, child := range dependents {
        \t\t\tchild.store.deleteWhere(child.key, id)
        \t\t}
        \t}
        \treturn ok
        }

        // deleteWhere deletes every item whose key refers to id (and, in turn, their children).
        func (s *Store) deleteWhere(key string, id int64) {
        \ts.mu.Lock()
        \tvar doomed []int64
        \tfor itemID, item := range s.items {
        \t\tif refersTo(item[key], id) {
        \t\t\tdoomed = append(doomed, itemID)
        \t\t}
        \t}
        \ts.mu.Unlock()
        \tfor _, itemID := range doomed {
        \t\ts.Delete(itemID)
        \t}
        }
        """)
    )


def _references(entity: Entity) -> list[tuple[str, str]]:
    """(parent, foreign key) pairs; ``entity.belongs_to`` is already normalised."""
    return [(parent, relations.foreign_key(parent)) for parent in entity.belongs_to]


def render_models(entities: list[Entity]) -> str:
    lines = ["package main", ""]
    for index, entity in enumerate(entities):
        rows = [("ID", "int64", '`json:"id"`')]
        for field in entity.fields:
            go_type = _GO_TYPES[field.type]
            rows.append((_go_name(field.name), go_type if field.required else f"*{go_type}",
                         f'`json:"{field.name}"`'))  # fmt: skip
        rows += [(_go_name(key), "int64", f'`json:"{key}"`') for _, key in _references(entity)]
        rows.append(("CreatedAt", "string", '`json:"created_at"`'))
        if index:
            lines.append("")
        lines += [
            f"// {entity.name} is the JSON shape of one {entity.name.lower()}.",
            f"type {entity.name} struct {{",
            *_aligned(rows),
            "}",
            "",
            f"var {_lower(entity.name)}Fields = []FieldSpec{{",
        ]
        for field in entity.fields:
            required = "true" if field.required else "false"
            lines.append(
                f'\t{{Name: "{field.name}", Type: {_FIELD_TYPES[field.type]}, Required: {required}}},'
            )
        lines += [
            f'\t{{Name: "{key}", Type: TypeInteger, Required: true}},'
            for _, key in _references(entity)
        ]
        lines.append("}")
    return "\n".join(lines) + "\n"


def render_handlers(entities: list[Entity]) -> str:
    routes = []
    for entity in relations.creation_order(entities):  # parents' stores exist before children's
        var = _lower(pluralize(entity.name))
        base = "/" + kebab(pluralize(entity.name))
        refs = "".join(
            f', reference{{key: "{key}", label: "{parent}", store: {_lower(pluralize(parent))}.store}}'
            for parent, key in _references(entity)
        )
        routes += [
            "",
            f'\t{var} := newResource("{entity.name}", {_lower(entity.name)}Fields{refs})',
            f'\tmux.HandleFunc("GET {base}", {var}.list)',
            f'\tmux.HandleFunc("POST {base}", {var}.create)',
            f'\tmux.HandleFunc("GET {base}/{{id}}", {var}.get)',
            f'\tmux.HandleFunc("PATCH {base}/{{id}}", {var}.update)',
            f'\tmux.HandleFunc("DELETE {base}/{{id}}", {var}.delete)',
        ]
    resource = _aligned([("label", "string"), ("fields", "[]FieldSpec"), ("store", "*Store"),
                         ("references", "[]reference")])  # fmt: skip
    reference = _aligned([("key", "string"), ("label", "string"), ("store", "*Store")])
    return (
        _src("""
        package main

        import (
        \t"encoding/json"
        \t"errors"
        \t"fmt"
        \t"net/http"
        \t"strconv"
        )

        const maxBodyBytes = 1 << 20

        type resource struct {
        """)
        + "\n".join(resource)
        + "\n}\n\n// reference is a many-to-one link: key holds the id of an item in the parent's store.\n"
        + "type reference struct {\n"
        + "\n".join(reference)
        + "\n}\n\n"
        + _src("""
        // newResource builds one entity's handlers; deleting a parent deletes what references it.
        func newResource(label string, fields []FieldSpec, references ...reference) *resource {
        \tres := &resource{label: label, fields: fields, store: NewStore(), references: references}
        \tfor _, ref := range references {
        \t\tref.store.CascadeTo(ref.key, res.store)
        \t}
        \treturn res
        }

        """)
        + _src("""
        // newServer wires every route: CRUD for each entity, plus GET /healthz.
        func newServer() http.Handler {
        \tmux := http.NewServeMux()
        \tmux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
        \t\twriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
        \t})
        """)
        + "\n".join(routes)
        + "\n\treturn mux\n}\n\n"
        + _src("""
        func writeJSON(w http.ResponseWriter, status int, body any) {
        \tw.Header().Set("Content-Type", "application/json")
        \tw.WriteHeader(status)
        \t_ = json.NewEncoder(w).Encode(body)
        }

        func detail(w http.ResponseWriter, status int, message any) {
        \twriteJSON(w, status, map[string]any{"detail": message})
        }

        func readBody(w http.ResponseWriter, r *http.Request) (map[string]any, bool) {
        \tvar body map[string]any
        \terr := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxBodyBytes)).Decode(&body)
        \tvar tooLarge *http.MaxBytesError
        \tswitch {
        \tcase errors.As(err, &tooLarge):
        \t\tdetail(w, http.StatusRequestEntityTooLarge, "Body too large")
        \t\treturn nil, false
        \tcase err != nil || body == nil:
        \t\tdetail(w, http.StatusUnprocessableEntity, "Body must be a JSON object")
        \t\treturn nil, false
        \t}
        \treturn body, true
        }

        func intParam(r *http.Request, name string, fallback int) (int, bool) {
        \traw := r.URL.Query().Get(name)
        \tif raw == "" {
        \t\treturn fallback, true
        \t}
        \tvalue, err := strconv.Atoi(raw)
        \treturn value, err == nil
        }

        func (res *resource) id(w http.ResponseWriter, r *http.Request) (int64, bool) {
        \tid, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
        \tif err != nil || id < 1 {
        \t\tdetail(w, http.StatusNotFound, res.label+" not found")
        \t\treturn 0, false
        \t}
        \treturn id, true
        }

        func (res *resource) list(w http.ResponseWriter, r *http.Request) {
        \tlimit, okLimit := intParam(r, "limit", 50)
        \toffset, okOffset := intParam(r, "offset", 0)
        \tif !okLimit || !okOffset || limit < 1 || limit > 200 || offset < 0 {
        \t\tdetail(w, http.StatusUnprocessableEntity, []string{"limit or offset out of range"})
        \t\treturn
        \t}
        \twhere := make(map[string]int64, len(res.references))
        \tfor _, ref := range res.references {
        \t\traw := r.URL.Query().Get(ref.key)
        \t\tif raw == "" {
        \t\t\tcontinue
        \t\t}
        \t\tid, err := strconv.ParseInt(raw, 10, 64)
        \t\tif err != nil || id < 1 {
        \t\t\tdetail(w, http.StatusUnprocessableEntity, []string{ref.key + ": expected a positive integer"})
        \t\t\treturn
        \t\t}
        \t\twhere[ref.key] = id
        \t}
        \twriteJSON(w, http.StatusOK, res.store.List(limit, offset, where))
        }

        // missingParent names the first referenced parent that does not exist.
        func (res *resource) missingParent(data map[string]any) (string, bool) {
        \tfor _, ref := range res.references {
        \t\tnumber, ok := data[ref.key].(float64)
        \t\tif !ok {
        \t\t\tcontinue
        \t\t}
        \t\tif _, found := ref.store.Get(int64(number)); !found {
        \t\t\treturn fmt.Sprintf("%s %d not found", ref.label, int64(number)), true
        \t\t}
        \t}
        \treturn "", false
        }

        func (res *resource) create(w http.ResponseWriter, r *http.Request) {
        \tbody, ok := readBody(w, r)
        \tif !ok {
        \t\treturn
        \t}
        \tdata, problems := validate(res.fields, body, false)
        \tif len(problems) > 0 {
        \t\tdetail(w, http.StatusUnprocessableEntity, problems)
        \t\treturn
        \t}
        \tif message, missing := res.missingParent(data); missing {
        \t\tdetail(w, http.StatusUnprocessableEntity, message)
        \t\treturn
        \t}
        \twriteJSON(w, http.StatusCreated, res.store.Create(data))
        }

        func (res *resource) get(w http.ResponseWriter, r *http.Request) {
        \tid, ok := res.id(w, r)
        \tif !ok {
        \t\treturn
        \t}
        \titem, found := res.store.Get(id)
        \tif !found {
        \t\tdetail(w, http.StatusNotFound, res.label+" not found")
        \t\treturn
        \t}
        \twriteJSON(w, http.StatusOK, item)
        }

        func (res *resource) update(w http.ResponseWriter, r *http.Request) {
        \tid, ok := res.id(w, r)
        \tif !ok {
        \t\treturn
        \t}
        \tbody, ok := readBody(w, r)
        \tif !ok {
        \t\treturn
        \t}
        \tdata, problems := validate(res.fields, body, true)
        \tif len(problems) > 0 {
        \t\tdetail(w, http.StatusUnprocessableEntity, problems)
        \t\treturn
        \t}
        \tif _, found := res.store.Get(id); !found {
        \t\tdetail(w, http.StatusNotFound, res.label+" not found")
        \t\treturn
        \t}
        \tif message, missing := res.missingParent(data); missing {
        \t\tdetail(w, http.StatusUnprocessableEntity, message)
        \t\treturn
        \t}
        \titem, found := res.store.Update(id, data)
        \tif !found {
        \t\tdetail(w, http.StatusNotFound, res.label+" not found")
        \t\treturn
        \t}
        \twriteJSON(w, http.StatusOK, item)
        }

        func (res *resource) delete(w http.ResponseWriter, r *http.Request) {
        \tid, ok := res.id(w, r)
        \tif !ok {
        \t\treturn
        \t}
        \tif !res.store.Delete(id) {
        \t\tdetail(w, http.StatusNotFound, res.label+" not found")
        \t\treturn
        \t}
        \tw.WriteHeader(http.StatusNoContent)
        }
        """)
    )


def render_main() -> str:
    return _src("""
        package main

        import (
        \t"flag"
        \t"log"
        \t"net/http"
        \t"os"
        \t"time"
        )

        func main() {
        \thealthcheck := flag.Bool("healthcheck", false, "probe /healthz on the local server and exit")
        \tflag.Parse()
        \tport := os.Getenv("PORT")
        \tif port == "" {
        \t\tport = "8000"
        \t}
        \tif *healthcheck {
        \t\tos.Exit(probe("http://127.0.0.1:" + port + "/healthz"))
        \t}
        \tserver := &http.Server{Addr: ":" + port, Handler: newServer(), ReadHeaderTimeout: 10 * time.Second}
        \tlog.Printf("listening on :%s", port)
        \tlog.Fatal(server.ListenAndServe())
        }

        // probe is the container health check: exit status 0 when /healthz answers 200.
        func probe(url string) int {
        \tclient := http.Client{Timeout: 2 * time.Second}
        \tresponse, err := client.Get(url)
        \tif err != nil {
        \t\treturn 1
        \t}
        \tdefer response.Body.Close()
        \tif response.StatusCode != http.StatusOK {
        \t\treturn 1
        \t}
        \treturn 0
        }
    """)


def render_test_helpers() -> str:
    return _src("""
        package main

        import (
        \t"bytes"
        \t"encoding/json"
        \t"net/http"
        \t"net/http/httptest"
        \t"testing"
        )

        func call(t *testing.T, server *httptest.Server, method, path string, body any) *http.Response {
        \tt.Helper()
        \tvar payload bytes.Buffer
        \tif body != nil {
        \t\tif err := json.NewEncoder(&payload).Encode(body); err != nil {
        \t\t\tt.Fatal(err)
        \t\t}
        \t}
        \trequest, err := http.NewRequest(method, server.URL+path, &payload)
        \tif err != nil {
        \t\tt.Fatal(err)
        \t}
        \trequest.Header.Set("Content-Type", "application/json")
        \tresponse, err := server.Client().Do(request)
        \tif err != nil {
        \t\tt.Fatal(err)
        \t}
        \tt.Cleanup(func() { response.Body.Close() })
        \treturn response
        }

        func expect(t *testing.T, response *http.Response, status int) {
        \tt.Helper()
        \tif response.StatusCode != status {
        \t\tt.Fatalf("%s %s: status %d, want %d", response.Request.Method, response.Request.URL.Path, response.StatusCode, status)
        \t}
        }

        func decode[T any](t *testing.T, response *http.Response, status int) T {
        \tt.Helper()
        \texpect(t, response, status)
        \tvar value T
        \tif err := json.NewDecoder(response.Body).Decode(&value); err != nil {
        \t\tt.Fatal(err)
        \t}
        \treturn value
        }

        // normalize gives a payload the shape JSON decoding produces (numbers as float64).
        func normalize(t *testing.T, value map[string]any) map[string]any {
        \tt.Helper()
        \tencoded, err := json.Marshal(value)
        \tif err != nil {
        \t\tt.Fatal(err)
        \t}
        \tvar decoded map[string]any
        \tif err := json.Unmarshal(encoded, &decoded); err != nil {
        \t\tt.Fatal(err)
        \t}
        \treturn decoded
        }

        func TestHealthz(t *testing.T) {
        \tserver := httptest.NewServer(newServer())
        \tdefer server.Close()
        \tbody := decode[map[string]string](t, call(t, server, http.MethodGet, "/healthz", nil), http.StatusOK)
        \tif body["status"] != "ok" {
        \t\tt.Fatalf("status = %q", body["status"])
        \t}
        }
    """)


def render_entity_test(entity: Entity, entities: list[Entity] | None = None) -> str:
    entities = entities or [entity]
    name = entity.name
    base = "/" + kebab(pluralize(name))
    payload = _lower(name) + "Payload"
    has_children = bool(relations.children_of(name, entities))
    # A child posts a body with freshly created parents; ``payload`` stays the plain sample.
    body = f"{_lower(name)}Body(t, server)" if entity.belongs_to else payload
    target = next((f for f in entity.fields if f.type in {"string", "text"}), entity.fields[0])
    new_value = _sample(target, updated=True)
    # JSON numbers decode to float64: compare against one, not an untyped int constant.
    expected = f"float64({new_value})" if target.type in {"integer", "float"} else new_value
    pairs = _aligned([(f'"{f.name}":', f"{_sample(f)},") for f in entity.fields])
    lines = [
        "package main",
        "",
        "import (",
        '\t"fmt"',
        *(['\t"maps"'] if entity.belongs_to else []),
        '\t"net/http"',
        '\t"net/http/httptest"',
        '\t"testing"',
        ")",
        "",
        f"var {payload} = map[string]any{{",
        *pairs,
        "}",
        "",
    ]
    if entity.belongs_to:
        lines += [
            f"// {_lower(name)}Body is {payload} pointing at parents created for the test.",
            f"func {_lower(name)}Body(t *testing.T, server *httptest.Server) map[string]any {{",
            "\tt.Helper()",
            f"\tbody := maps.Clone({payload})",
            *[f'\tbody["{key}"] = make{parent}(t, server)' for parent, key in _references(entity)],
            "\treturn body",
            "}",
            "",
        ]
    if has_children:
        lines += [
            f"// make{name} creates a {name.lower()} and returns its id, for tests of what belongs to it.",
            f"func make{name}(t *testing.T, server *httptest.Server) float64 {{",
            "\tt.Helper()",
            f'\tcreated := decode[map[string]any](t, call(t, server, http.MethodPost, "{base}", {body}), http.StatusCreated)',
            '\treturn created["id"].(float64)',
            "}",
            "",
        ]
    lines += [
        f"func Test{name}CreateAndGet(t *testing.T) {{",
        "\tserver := httptest.NewServer(newServer())",
        "\tdefer server.Close()",
        *([f"\tsent := {body}"] if entity.belongs_to else []),
        f'\tcreated := decode[map[string]any](t, call(t, server, http.MethodPost, "{base}", {"sent" if entity.belongs_to else payload}), http.StatusCreated)',
        f"\tfor key, want := range normalize(t, {'sent' if entity.belongs_to else payload}) {{",
        "\t\tif created[key] != want {",
        '\t\t\tt.Errorf("%s = %v, want %v", key, created[key], want)',
        "\t\t}",
        "\t}",
        f'\tfetched := decode[map[string]any](t, call(t, server, http.MethodGet, fmt.Sprintf("{base}/%v", created["id"]), nil), http.StatusOK)',
        '\tif fetched["id"] != created["id"] {',
        '\t\tt.Fatalf("id = %v, want %v", fetched["id"], created["id"])',
        "\t}",
        "}",
        "",
        f"func Test{name}ListPaginates(t *testing.T) {{",
        "\tserver := httptest.NewServer(newServer())",
        "\tdefer server.Close()",
        "\tfor range 3 {",
        f'\t\texpect(t, call(t, server, http.MethodPost, "{base}", {body}), http.StatusCreated)',
        "\t}",
        f'\tpage := decode[[]map[string]any](t, call(t, server, http.MethodGet, "{base}?limit=2&offset=1", nil), http.StatusOK)',
        "\tif len(page) != 2 {",
        '\t\tt.Fatalf("page has %d items, want 2", len(page))',
        "\t}",
        f'\texpect(t, call(t, server, http.MethodGet, "{base}?limit=0", nil), http.StatusUnprocessableEntity)',
        "}",
        "",
        f"func Test{name}Update(t *testing.T) {{",
        "\tserver := httptest.NewServer(newServer())",
        "\tdefer server.Close()",
        f'\tcreated := decode[map[string]any](t, call(t, server, http.MethodPost, "{base}", {body}), http.StatusCreated)',
        f'\tpath := fmt.Sprintf("{base}/%v", created["id"])',
        f'\tupdated := decode[map[string]any](t, call(t, server, http.MethodPatch, path, map[string]any{{"{target.name}": {_sample(target, updated=True)}}}), http.StatusOK)',
        f'\tif updated["{target.name}"] != {expected} {{',
        f'\t\tt.Fatalf("{target.name} = %v", updated["{target.name}"])',
        "\t}",
        "}",
        "",
        f"func Test{name}Delete(t *testing.T) {{",
        "\tserver := httptest.NewServer(newServer())",
        "\tdefer server.Close()",
        f'\tcreated := decode[map[string]any](t, call(t, server, http.MethodPost, "{base}", {body}), http.StatusCreated)',
        f'\tpath := fmt.Sprintf("{base}/%v", created["id"])',
        "\texpect(t, call(t, server, http.MethodDelete, path, nil), http.StatusNoContent)",
        "\texpect(t, call(t, server, http.MethodGet, path, nil), http.StatusNotFound)",
        "}",
        "",
        f"func Test{name}UnknownIDIs404(t *testing.T) {{",
        "\tserver := httptest.NewServer(newServer())",
        "\tdefer server.Close()",
        f'\texpect(t, call(t, server, http.MethodGet, "{base}/999999", nil), http.StatusNotFound)',
        f'\texpect(t, call(t, server, http.MethodGet, "{base}/not-a-number", nil), http.StatusNotFound)',
        "}",
        "",
        f"func Test{name}RejectsInvalidInput(t *testing.T) {{",
        "\tserver := httptest.NewServer(newServer())",
        "\tdefer server.Close()",
        f'\texpect(t, call(t, server, http.MethodPost, "{base}", map[string]any{{"unknown_field": 1}}), http.StatusUnprocessableEntity)',
    ]
    if any(f.required for f in entity.fields) or entity.belongs_to:
        lines.append(
            f'\texpect(t, call(t, server, http.MethodPost, "{base}", map[string]any{{}}), '
            "http.StatusUnprocessableEntity)"
        )
    lines.append("}")
    if entity.belongs_to:
        parent, key = _references(entity)[0]
        parent_base = "/" + kebab(pluralize(parent))
        lines += [
            "",
            f"func Test{name}RequiresAnExisting{parent}(t *testing.T) {{",
            "\tserver := httptest.NewServer(newServer())",
            "\tdefer server.Close()",
            f"\tsent := {body}",
            f'\tsent["{key}"] = 999999',
            f'\tgot := decode[map[string]any](t, call(t, server, http.MethodPost, "{base}", sent), http.StatusUnprocessableEntity)',
            f'\tif got["detail"] != "{parent} 999999 not found" {{',
            '\t\tt.Fatalf("detail = %v", got["detail"])',
            "\t}",
            "}",
            "",
            f"func Test{name}FiltersBy{parent}(t *testing.T) {{",
            "\tserver := httptest.NewServer(newServer())",
            "\tdefer server.Close()",
            f"\tmine := {body}",
            f'\texpect(t, call(t, server, http.MethodPost, "{base}", mine), http.StatusCreated)',
            f'\texpect(t, call(t, server, http.MethodPost, "{base}", {body}), http.StatusCreated)',
            f'\tpage := decode[[]map[string]any](t, call(t, server, http.MethodGet, fmt.Sprintf("{base}?{key}=%v", mine["{key}"]), nil), http.StatusOK)',
            f'\tif len(page) != 1 || page[0]["{key}"] != mine["{key}"] {{',
            '\t\tt.Fatalf("page = %v", page)',
            "\t}",
            f'\texpect(t, call(t, server, http.MethodGet, "{base}?{key}=0", nil), http.StatusUnprocessableEntity)',
            "}",
            "",
            f"func Test{name}IsDeletedWithIts{parent}(t *testing.T) {{",
            "\tserver := httptest.NewServer(newServer())",
            "\tdefer server.Close()",
            f'\tcreated := decode[map[string]any](t, call(t, server, http.MethodPost, "{base}", {body}), http.StatusCreated)',
            f'\texpect(t, call(t, server, http.MethodDelete, fmt.Sprintf("{parent_base}/%v", created["{key}"]), nil), http.StatusNoContent)',
            f'\texpect(t, call(t, server, http.MethodGet, fmt.Sprintf("{base}/%v", created["id"]), nil), http.StatusNotFound)',
            "}",
        ]
    return "\n".join(lines) + "\n"


def render_readme(name: str, summary: str, architecture: ArchitectureSpec) -> str:
    rows = "\n".join(f"| `{e.method}` | `{e.path}` | {e.summary} |" for e in architecture.endpoints)
    return (
        _src(f"""
        # {name}

        {summary}

        > Generated by the Autonomous AI Software Engineering Platform (offline provider).
        > Review the code before deploying it anywhere that matters.

        Go {GO_VERSION} with the standard library only. Data is kept in memory, so run one
        replica until a database replaces `store.go`. A record that belongs to another (a
        `<parent>_id` field) must point at one that exists, lists can be filtered by it, and
        deleting the parent deletes its children.

        ## Run and test

        ```bash
        go run .             # http://localhost:8000
        gofmt -l . && go vet ./... && go test ./...
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
        GeneratedFile(path="go.mod", content=render_go_mod(project),
                      description="Module definition (no dependencies)"),
        GeneratedFile(path="README.md", content=render_readme(project, summary, architecture),
                      description="How to run, test and deploy the service"),
        GeneratedFile(path="main.go", content=render_main(),
                      description="Process entry point and container health check"),
        GeneratedFile(path="handlers.go", content=render_handlers(entities),
                      description="HTTP routing and CRUD handlers"),
        GeneratedFile(path="models.go", content=render_models(entities),
                      description="Entity types and field lists"),
        GeneratedFile(path="validation.go", content=render_validation(),
                      description="Field-driven input validation"),
        GeneratedFile(path="store.go", content=render_store(),
                      description="In-memory storage per entity"),
        GeneratedFile(path="helpers_test.go", content=render_test_helpers(),
                      description="Test helpers and the health endpoint test"),
    ]  # fmt: skip
    for entity in entities:
        files.append(
            GeneratedFile(
                path=f"{snake(pluralize(entity.name))}_test.go",
                content=render_entity_test(entity, entities),
                description=f"API tests for {pluralize(entity.name).lower()}",
            )
        )
    return files
