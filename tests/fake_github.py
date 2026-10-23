"""A fake of the GitHub REST endpoints AutoSWE's publisher uses.

It keeps repositories, git objects, refs and pull requests in memory and reproduces the behaviour
the publisher depends on: bearer-token auth (401 "Bad credentials"), a 422 for a taken name,
``auto_init`` finishing a moment after the repository is created (the default branch's ref
404s for the first reads), content-addressed trees, a 403 when a ref would add a workflow file
without the Workflows permission, rate-limit responses, and injected failures.

Run it standalone for a manual end-to-end check (no real repositories are ever created):

    uv run python -m tests.fake_github --port 5366 --token fake-token-0123456789
    GITHUB_PUBLISH_TOKEN=fake-token-0123456789 GITHUB_API_URL=http://127.0.0.1:5366 \\
        uv run autoswe serve
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

PERMISSIONS = frozenset({"administration", "contents", "workflows", "pull_requests"})


@dataclass
class FakeRepo:
    full_name: str
    private: bool
    description: str
    default_branch: str = "main"
    refs: dict[str, str] = field(default_factory=dict)
    pulls: list[dict[str, Any]] = field(default_factory=list)
    reads_until_ready: int = 0


@dataclass
class FakeGitHub:
    token: str
    login: str = "octo-demo"
    permissions: frozenset[str] = PERMISSIONS
    init_delay_reads: int = 1  # the default branch's ref 404s this many times after creation
    repos: dict[str, FakeRepo] = field(default_factory=dict)
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    # endpoint name -> status codes to answer with first (e.g. {"pulls": [500, 500]})
    failures: dict[str, list[int]] = field(default_factory=dict)

    def store(self, kind: str, payload: dict[str, Any]) -> str:
        sha = hashlib.sha1(
            json.dumps({"kind": kind, **payload}, sort_keys=True).encode()
        ).hexdigest()
        self.objects[sha] = {"kind": kind, **payload}
        return sha

    def files_at(self, repo: str, branch: str) -> dict[str, str]:
        commit = self.objects[self.repos[repo].refs[branch]]
        return dict(self.objects[commit["tree"]]["files"])


def _error(status: int, message: str, errors: list[dict[str, Any]] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"message": message}
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status)


def create_fake_github(state: FakeGitHub) -> FastAPI:
    app = FastAPI(title="fake GitHub API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.github = state

    def check(request: Request, endpoint: str, authorization: str | None) -> JSONResponse | None:
        state.requests.append(
            {"endpoint": endpoint, "method": request.method, "path": request.url.path,
             "headers": dict(request.headers)}
        )  # fmt: skip
        if authorization != f"Bearer {state.token}":
            return _error(401, "Bad credentials")
        if request.headers.get("x-github-api-version") != "2022-11-28":
            return _error(400, "missing or unsupported X-GitHub-Api-Version")
        queued = state.failures.get(endpoint)
        if queued:
            code = queued.pop(0)
            if code == 403:
                return JSONResponse(
                    {"message": "API rate limit exceeded"}, status_code=403,
                    headers={"x-ratelimit-remaining": "0", "retry-after": "30"},
                )  # fmt: skip
            return _error(code, f"injected failure {code}")
        return None

    def repo_or_404(owner: str, name: str) -> FakeRepo | JSONResponse:
        repo = state.repos.get(f"{owner}/{name}")
        return repo if repo is not None else _error(404, "Not Found")

    async def create(request: Request, owner: str, auth: str | None, endpoint: str) -> Any:
        if (denied := check(request, endpoint, auth)) is not None:
            return denied
        if "administration" not in state.permissions:
            return _error(403, "Resource not accessible by personal access token")
        body = await request.json()
        full_name = f"{owner}/{body['name']}"
        if full_name in state.repos:
            return _error(422, "Repository creation failed.", errors=[
                {"resource": "Repository", "code": "custom", "field": "name",
                 "message": "name already exists on this account"}])  # fmt: skip
        repo = FakeRepo(full_name=full_name, private=bool(body.get("private")),
                        description=str(body.get("description", "")),
                        reads_until_ready=state.init_delay_reads)  # fmt: skip
        if body.get("auto_init"):
            tree = state.store("tree", {"files": {"README.md": f"# {body['name']}\n"}})
            repo.refs["main"] = state.store("commit", {"tree": tree, "parents": [],
                                                       "message": "Initial commit"})  # fmt: skip
        state.repos[full_name] = repo
        return JSONResponse(status_code=201, content={
            "full_name": full_name, "name": body["name"], "private": repo.private,
            "html_url": f"https://github.example/{full_name}", "default_branch": "main",
            "owner": {"login": owner},
        })  # fmt: skip

    @app.get("/user")
    async def user(request: Request, authorization: str | None = Header(None)) -> Any:
        return check(request, "user", authorization) or {"login": state.login}

    @app.post("/user/repos")
    async def create_user_repo(request: Request, authorization: str | None = Header(None)) -> Any:
        return await create(request, state.login, authorization, "create_repo")

    @app.post("/orgs/{org}/repos")
    async def create_org_repo(
        org: str, request: Request, authorization: str | None = Header(None)
    ) -> Any:
        return await create(request, org, authorization, "create_repo")

    @app.get("/repos/{owner}/{name}/git/ref/heads/{branch:path}")
    async def get_ref(owner: str, name: str, branch: str, request: Request,
                      authorization: str | None = Header(None)) -> Any:  # fmt: skip
        if (denied := check(request, "ref", authorization)) is not None:
            return denied
        repo = repo_or_404(owner, name)
        if isinstance(repo, JSONResponse):
            return repo
        if repo.reads_until_ready > 0:  # auto_init has not finished yet
            repo.reads_until_ready -= 1
            return _error(409, "Git Repository is empty.")
        if branch not in repo.refs:
            return _error(404, "Not Found")
        return {"ref": f"refs/heads/{branch}", "object": {"sha": repo.refs[branch],
                                                          "type": "commit"}}  # fmt: skip

    @app.get("/repos/{owner}/{name}/git/commits/{sha}")
    async def get_commit(owner: str, name: str, sha: str, request: Request,
                         authorization: str | None = Header(None)) -> Any:  # fmt: skip
        if (denied := check(request, "commit", authorization)) is not None:
            return denied
        commit = state.objects.get(sha)
        if commit is None or commit["kind"] != "commit":
            return _error(404, "Not Found")
        return {"sha": sha, "tree": {"sha": commit["tree"]}}

    @app.post("/repos/{owner}/{name}/git/trees")
    async def create_tree(owner: str, name: str, request: Request,
                          authorization: str | None = Header(None)) -> Any:  # fmt: skip
        if (denied := check(request, "tree", authorization)) is not None:
            return denied
        body = await request.json()
        base = state.objects.get(body.get("base_tree") or "", {"files": {}})
        files = dict(base.get("files", {}))
        for entry in body["tree"]:
            if entry.get("mode") != "100644" or entry.get("type") != "blob":
                return _error(422, "tree.mode/type not supported by the fake")
            if "content" in entry and "sha" in entry:
                return _error(422, "content and sha are mutually exclusive")
            files[entry["path"]] = entry["content"]
        return JSONResponse(status_code=201,
                            content={"sha": state.store("tree", {"files": files})})  # fmt: skip

    @app.post("/repos/{owner}/{name}/git/commits")
    async def create_commit(owner: str, name: str, request: Request,
                            authorization: str | None = Header(None)) -> Any:  # fmt: skip
        if (denied := check(request, "create_commit", authorization)) is not None:
            return denied
        body = await request.json()
        if body["tree"] not in state.objects:
            return _error(422, "Tree SHA does not exist")
        sha = state.store("commit", {"tree": body["tree"], "parents": body["parents"],
                                     "message": body["message"]})  # fmt: skip
        return JSONResponse(status_code=201, content={"sha": sha})

    @app.post("/repos/{owner}/{name}/git/refs")
    async def create_ref(owner: str, name: str, request: Request,
                         authorization: str | None = Header(None)) -> Any:  # fmt: skip
        if (denied := check(request, "create_ref", authorization)) is not None:
            return denied
        repo = repo_or_404(owner, name)
        if isinstance(repo, JSONResponse):
            return repo
        body = await request.json()
        branch = body["ref"].removeprefix("refs/heads/")
        if branch in repo.refs:
            return _error(422, "Reference already exists")
        commit = state.objects[body["sha"]]
        files = state.objects[commit["tree"]]["files"]
        if "workflows" not in state.permissions and any(
            path.startswith(".github/workflows/") for path in files
        ):
            return _error(403, "refusing to allow a Personal Access Token to create or update "
                               "workflow without `workflows` permission")  # fmt: skip
        repo.refs[branch] = body["sha"]
        return JSONResponse(status_code=201, content={"ref": body["ref"],
                                                      "object": {"sha": body["sha"]}})  # fmt: skip

    @app.post("/repos/{owner}/{name}/pulls")
    async def create_pull(owner: str, name: str, request: Request,
                          authorization: str | None = Header(None)) -> Any:  # fmt: skip
        if (denied := check(request, "pulls", authorization)) is not None:
            return denied
        repo = repo_or_404(owner, name)
        if isinstance(repo, JSONResponse):
            return repo
        body = await request.json()
        if body["head"] not in repo.refs or body["base"] not in repo.refs:
            return _error(422, "Validation Failed", errors=[{"message": "head or base missing"}])
        number = len(repo.pulls) + 1
        repo.pulls.append({"number": number, **body})
        return JSONResponse(status_code=201, content={
            "number": number, "html_url": f"https://github.example/{repo.full_name}/pull/{number}",
        })  # fmt: skip

    return app


def main() -> None:  # pragma: no cover - manual end-to-end checks
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5366)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    uvicorn.run(create_fake_github(FakeGitHub(token=args.token)), host="127.0.0.1", port=args.port)


if __name__ == "__main__":  # pragma: no cover
    main()
