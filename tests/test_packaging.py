import io
import zipfile

from autoswe.pipeline.packaging import build_zip


def test_zip_has_project_root_and_is_deterministic() -> None:
    files = [("b.py", "print('b')\n"), ("a/README.md", "# a\n")]
    first = build_zip("My Project", files)
    second = build_zip("My Project", list(reversed(files)))
    assert first == second  # stable ordering and timestamps

    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["my-project/a/README.md", "my-project/b.py"]
        assert archive.read("my-project/b.py") == b"print('b')\n"
        assert archive.testzip() is None


def test_zip_skips_unsafe_paths() -> None:
    data = build_zip("p", [("../../etc/passwd", "x"), ("ok.txt", "y")])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["p/ok.txt"]
