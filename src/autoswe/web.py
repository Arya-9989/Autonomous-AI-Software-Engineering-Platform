"""Serving the built UI: hashed assets, the HTML pages and the hashes of their inline scripts.

``npm run build`` writes ``frontend/dist``: prerendered HTML pages (see :func:`page_for`), hashed
files under ``assets/``, and Brotli (``.br``) and gzip (``.gz``) copies of the text files.
Hashed assets never change, so they are cached for a year as immutable; HTML is revalidated on
every load. Each response is the smallest encoding the client accepts, with nothing compressed
per request. Paths under ``/api`` and ``/assets`` are never answered with a page.

A path that is not one of the client router's routes is answered with **HTTP 404** and the
page the router renders as "Page not found" (a prerendered ``404.html`` if the build has one,
else the shell), so crawlers and monitors see a real 404 while people still see the designed
page. :data:`CLIENT_ROUTES` mirrors ``frontend/src/App.tsx`` and ``AppShell.tsx``; a test keeps
the two in step.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse

IMMUTABLE = "public, max-age=31536000, immutable"
# Preferred first: Brotli is roughly 15-20% smaller than gzip on these bundles.
ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
# Explicit types: Python's tables come from the OS on Windows and lack woff2 (and may map .js
# to text/plain, which browsers refuse for module scripts).
_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".json": "application/json",
    ".webp": "image/webp",
    ".png": "image/png",
}
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL)


def media_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    return _TYPES.get(path.suffix.lower()) or guessed or "application/octet-stream"


def accepted_encodings(header: str) -> set[str]:
    """Content codings the client accepts (``q=0`` excludes one)."""
    accepted: set[str] = set()
    for item in header.split(","):
        coding, _, params = item.strip().partition(";")
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if coding and quality > 0:
            accepted.add(coding.lower())
    return accepted


def inline_script_hashes(index_html: str) -> list[str]:
    """CSP ``sha256-`` sources for each inline ``<script>`` in the built index.html."""
    return [
        "sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        for body in _INLINE_SCRIPT.findall(index_html)
    ]


def _inside(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    if relative and candidate.is_file() and candidate.is_relative_to(root.resolve()):
        return candidate
    return None


def _send(
    file: Path, request: Request, cache_control: str, status_code: int = status.HTTP_200_OK
) -> FileResponse:
    """``file``, or its smallest precompressed variant the client accepts."""
    headers = {"Cache-Control": cache_control}
    variants = [
        (coding, file.with_name(file.name + suffix))
        for coding, suffix in ENCODINGS
        if file.with_name(file.name + suffix).is_file()
    ]
    if variants:
        headers["Vary"] = "Accept-Encoding"
        accepted = accepted_encodings(request.headers.get("accept-encoding", ""))
        for coding, variant in variants:
            if coding in accepted:
                headers["Content-Encoding"] = coding
                return FileResponse(
                    variant, status_code=status_code, media_type=media_type(file), headers=headers
                )
    return FileResponse(file, status_code=status_code, media_type=media_type(file), headers=headers)


# Which HTML document each route gets (see frontend/scripts/prerender.mjs). Each one hydrates
# exactly what it was prerendered with, so a route may only get a page rendered for it.
PAGES = ("index.html", "app.html", "job.html", "shell.html")
NOT_FOUND_PAGE = "404.html"
_JOB_ROUTE = re.compile(r"^app/jobs/[^/]+/?$")
# Paths (without the leading slash) the client router renders a real page for: "/" (landing),
# "/app" (new project), "/app/jobs/:jobId" (a job) and "/jobs/:jobId" (legacy, redirects).
CLIENT_ROUTES = (
    re.compile(r"^$"),
    re.compile(r"^app/?$"),
    _JOB_ROUTE,
    re.compile(r"^jobs/[^/]+/?$"),
)


def is_client_route(full_path: str) -> bool:
    return any(route.match(full_path) for route in CLIENT_ROUTES)


def html_pages(static_dir: Path) -> list[Path]:
    """The UI's HTML documents that exist in this build."""
    return [static_dir / name for name in PAGES if (static_dir / name).is_file()]


def page_for(full_path: str) -> str:
    """The document for a client route: prerendered where possible, else the empty shell."""
    if full_path in {"", "index.html"}:
        return "index.html"
    if full_path in {"app", "app/"}:
        return "app.html"
    if _JOB_ROUTE.match(full_path):
        return "job.html"
    return "shell.html"


def mount_frontend(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    shell = static_dir / "shell.html" if (static_dir / "shell.html").is_file() else index
    not_found = static_dir / NOT_FOUND_PAGE
    not_found_page = not_found if not_found.is_file() else shell
    assets = static_dir / "assets"

    def document(name: str) -> Path:
        page = static_dir / name
        # A build without prerendered studio pages (or a test fixture) serves the shell instead.
        return page if name in PAGES and page.is_file() else shell

    @app.api_route("/assets/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def asset(path: str, request: Request) -> FileResponse:  # sync: runs in the threadpool
        file = _inside(assets, path)
        if file is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return _send(file, request, IMMUTABLE)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(full_path: str, request: Request) -> FileResponse:
        if full_path in {"api", "assets"} or full_path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        if full_path in PAGES:
            return _send(document(full_path), request, "no-cache")
        name = page_for(full_path)
        if name == "shell.html":  # a top-level file of the build (favicon.svg, robots.txt)?
            file = _inside(static_dir, full_path)
            if file is not None and file.suffix not in {".br", ".gz"}:
                return FileResponse(file, media_type=media_type(file))
        if not is_client_route(full_path):
            return _send(not_found_page, request, "no-cache", status.HTTP_404_NOT_FOUND)
        return _send(document(name), request, "no-cache")
