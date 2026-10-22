"""Serving the built UI: precompressed immutable assets and the inline-script CSP."""

from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

import pytest

from autoswe.web import accepted_encodings, inline_script_hashes, page_for
from tests.conftest import AppHarness, HarnessFactory

SCRIPT = "document.documentElement.classList.add('dark')"
INDEX = (
    "<!doctype html><html><head><title>ui</title>"
    f"<script>{SCRIPT}</script>"
    '<script type="module" src="/assets/app.js"></script>'
    "</head><body></body></html>"
)
JS = "console.log('hello');\n" * 50


@pytest.fixture
async def ui(harness_factory: HarnessFactory, tmp_path: Path) -> AppHarness:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(INDEX.replace("<body>", "<body><h1>Prerendered</h1>"))
    (dist / "index.html.br").write_bytes(b"pretend-brotli-page")
    (dist / "shell.html").write_text(INDEX)
    (dist / "job.html").write_text(INDEX.replace("<body>", "<body><p>Loading job</p>"))
    (assets / "app.js").write_bytes(JS.encode())
    (assets / "app.js.gz").write_bytes(gzip.compress(JS.encode()))
    (assets / "app.js.br").write_bytes(b"pretend-brotli")
    (assets / "font.woff2").write_bytes(b"wOF2")
    (assets / "plain.css").write_text("body{}")
    return await harness_factory(static_dir=dist, worker_enabled=False)  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("accept", "encoding", "body"),
    [
        ("gzip, deflate, br, zstd", "br", b"pretend-brotli"),
        ("gzip", "gzip", gzip.compress(JS.encode())),
        ("br;q=0, gzip;q=0.8", "gzip", gzip.compress(JS.encode())),
        ("identity", None, JS.encode()),
    ],
)
async def test_assets_are_served_precompressed(
    ui: AppHarness, accept: str, encoding: str | None, body: bytes
) -> None:
    # httpx would transparently decode gzip; ask for the raw bytes to see what was sent.
    request = ui.client.build_request("GET", "/assets/app.js", headers={"Accept-Encoding": accept})
    response = await ui.client.send(request, stream=True)
    raw = b"".join([chunk async for chunk in response.aiter_raw()])
    await response.aclose()
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == encoding
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    if encoding == "gzip":
        assert gzip.decompress(raw) == JS.encode()
    else:
        assert raw == body


async def test_assets_without_variants_and_fonts(ui: AppHarness) -> None:
    css = await ui.client.get("/assets/plain.css", headers={"Accept-Encoding": "br"})
    assert css.status_code == 200
    assert "content-encoding" not in css.headers
    assert "vary" not in css.headers
    font = await ui.client.get("/assets/font.woff2")
    assert font.headers["content-type"] == "font/woff2"
    assert font.headers["cache-control"].endswith("immutable")
    assert (await ui.client.head("/assets/app.js")).status_code == 200


@pytest.mark.parametrize(
    "path", ["/assets/missing.js", "/assets", "/assets/", "/assets/..%2fdist%2findex.html", "/api"]
)
async def test_missing_assets_and_api_paths_are_404(ui: AppHarness, path: str) -> None:
    response = await ui.client.get(path)
    assert response.status_code == 404
    assert "<title>ui</title>" not in response.text


async def test_inline_scripts_are_allowed_by_hash_only(ui: AppHarness) -> None:
    page = await ui.client.get("/app/jobs/123")
    assert page.headers["cache-control"] == "no-cache"
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    csp = page.headers["content-security-policy"]
    assert f"script-src 'self' 'sha256-{digest}';" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]


def test_inline_script_hashes_skip_external_scripts() -> None:
    html = '<script src="/x.js"></script><script>a()</script><script type="m">b()</script>'
    expected = [
        "sha256-" + base64.b64encode(hashlib.sha256(body).digest()).decode()
        for body in (b"a()", b"b()")
    ]
    assert inline_script_hashes(html) == expected


def test_built_index_inline_scripts_match_the_csp() -> None:
    """The real build: every inline script in frontend/dist/index.html gets a hash."""
    index = Path(__file__).resolve().parents[1] / "frontend" / "dist" / "index.html"
    if not index.is_file():
        pytest.skip("frontend not built")
    html = index.read_text(encoding="utf-8")
    assert len(inline_script_hashes(html)) == html.count("<script>")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("gzip, deflate, br", {"gzip", "deflate", "br"}),
        ("br;q=0, gzip;q=1.0", {"gzip"}),
        ("BR; q=0.5", {"br"}),
        ("gzip;q=oops", set()),
        ("", set()),
    ],
)
def test_accept_encoding_parsing(header: str, expected: set[str]) -> None:
    assert accepted_encodings(header) == expected


async def test_head_requests_are_answered(ui: AppHarness) -> None:
    for path in ("/", "/app", "/assets/app.js"):
        response = await ui.client.head(path)
        assert response.status_code == 200, path
        assert response.content == b""


async def test_each_route_gets_the_page_prerendered_for_it(ui: AppHarness) -> None:
    landing = await ui.client.get("/", headers={"Accept-Encoding": "identity"})
    assert "<h1>Prerendered</h1>" in landing.text
    assert landing.headers["cache-control"] == "no-cache"
    for path in ("/app/jobs/123", "/app/jobs/123/"):
        assert "Loading job" in (await ui.client.get(path)).text, path
    # No app.html in this build: /app falls back to the shell, like every other route.
    for path in ("/app", "/app/jobs/1/files", "/jobs/123", "/no-such-page", "/shell.html"):
        shell = await ui.client.get(path)
        assert shell.status_code == 200, path
        assert "<title>ui</title>" in shell.text, path
        assert "Prerendered" not in shell.text and "Loading job" not in shell.text, path
    request = ui.client.build_request("GET", "/", headers={"Accept-Encoding": "br"})
    response = await ui.client.send(request, stream=True)
    raw = b"".join([chunk async for chunk in response.aiter_raw()])
    await response.aclose()
    assert response.headers["content-encoding"] == "br"
    assert raw == b"pretend-brotli-page"
    assert response.headers["content-type"].startswith("text/html")
    # Precompressed variants are never served under their own names.
    assert "pretend" not in (await ui.client.get("/index.html.br")).text


@pytest.mark.parametrize(
    ("path", "page"),
    [
        ("", "index.html"),
        ("app", "app.html"),
        ("app/", "app.html"),
        ("app/jobs/42", "job.html"),
        ("app/jobs/42/", "job.html"),
        ("app/jobs/42/files", "shell.html"),
        ("app/settings", "shell.html"),
        ("jobs/42", "shell.html"),
        ("anything", "shell.html"),
    ],
)
def test_routes_map_to_prerendered_pages(path: str, page: str) -> None:
    assert page_for(path) == page
