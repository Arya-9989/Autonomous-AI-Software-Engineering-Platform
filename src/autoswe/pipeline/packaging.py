"""Build the downloadable archive for a finished job."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable

from autoswe.pipeline.naming import kebab
from autoswe.pipeline.validation import normalize_path

# Fixed timestamp => byte-identical archives for identical content (cacheable, diffable).
_EPOCH = (1980, 1, 1, 0, 0, 0)


def build_zip(project_name: str, files: Iterable[tuple[str, str]]) -> bytes:
    root = kebab(project_name or "project")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files):
            safe = normalize_path(path)
            if safe is None:  # defence in depth: paths were already sanitised on ingest
                continue
            info = zipfile.ZipInfo(f"{root}/{safe}", date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()
