from __future__ import annotations

import gzip
import os
import urllib.request
from pathlib import Path

import pytest
from layout_studio import Layout, Segment, model


class _Response:
    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload

    def __enter__(self) -> _Response:  # noqa: PYI034
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_json_text_and_location_sources_are_explicit(tmp_path):
    segment = Segment(2.0, 0.25, -0.5)
    text = segment.to_json(indent=None)

    assert Segment.from_json(text=text) == segment
    assert Segment.from_json(None, text.encode()) == segment
    assert Segment.from_json(text=bytearray(text.encode())) == segment
    assert segment.to_json(str, indent=None) == text

    filename = tmp_path / "segment.json"
    assert segment.to_json(filename, indent=None) is None
    assert Segment.from_json(filename) == segment

    class Filename(str):
        pass

    subclass_filename = Filename(str(tmp_path / "subclass.json"))
    assert segment.to_json(subclass_filename) is None
    assert Segment.from_json(subclass_filename) == segment

    with pytest.raises(TypeError, match="exactly one"):
        Segment.from_json()
    with pytest.raises(TypeError, match="exactly one"):
        Segment.from_json(filename, text)

    assert not hasattr(Segment, "load")
    assert not hasattr(segment, "save")


def test_json_gzip_paths_suffix_and_magic(tmp_path):
    segment = Segment(3.0, -0.1, 0.2)
    filename = tmp_path / "segment.json.gz"

    assert segment.to_json(filename, indent=None) is None
    compressed = filename.read_bytes()
    assert compressed.startswith(b"\x1f\x8b")
    assert Segment.from_json(filename) == segment
    assert Segment.from_json(text=compressed) == segment

    magic_only = tmp_path / "segment.data"
    magic_only.write_bytes(compressed)
    assert Segment.from_json(magic_only) == segment

    corrupt = tmp_path / "corrupt.json.gz"
    corrupt.write_bytes(b"not gzip")
    with pytest.raises(gzip.BadGzipFile):
        Segment.from_json(corrupt)


def test_json_http_get_and_put_are_gzip_and_query_aware(monkeypatch):
    segment = Segment(4.0, 0.3, -0.4)
    text = segment.to_json(indent=None)
    compressed = gzip.compress(text.encode(), mtime=0)
    requests: list[str | urllib.request.Request] = []

    def fake_urlopen(target: str | urllib.request.Request) -> _Response:
        requests.append(target)
        if isinstance(target, urllib.request.Request):
            return _Response()
        return _Response(compressed)

    monkeypatch.setattr(model.urllib.request, "urlopen", fake_urlopen)

    source_url = "https://example.test/layout.json.gz?token=read"
    assert Segment.from_json(source_url) == segment

    target_url = "https://example.test/layout.json.gz?token=write"
    assert segment.to_json(target_url, indent=None) is None

    assert requests[0] == source_url
    request = requests[1]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == target_url
    assert request.get_method() == "PUT"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Content-encoding") == "gzip"
    assert gzip.decompress(request.data) == text.encode()


def test_json_custom_pathlike_and_unicode_round_trip(tmp_path, canonical_layout_dict):
    class CustomPath(os.PathLike[str]):
        def __init__(self, path: Path) -> None:
            self.path = path

        def __fspath__(self) -> str:
            return os.fspath(self.path)

    curve = canonical_layout_dict["reference_curves"].pop("main")
    canonical_layout_dict["reference_curves"]["café-环🚀"] = curve
    canonical_layout_dict["objects"]["Q1"]["position"]["reference"]["curve"] = (
        "café-环🚀"
    )
    layout = Layout.from_dict(canonical_layout_dict)
    filename = CustomPath(tmp_path / "Δ-元件.json")

    assert layout.to_json(filename, indent=None) is None
    assert Layout.from_json(filename).to_dict() == canonical_layout_dict

    text = layout.to_json(str, indent=None)
    assert Layout.from_json(None, text).to_dict() == canonical_layout_dict


def test_actual_string_is_a_target_while_builtin_str_is_the_only_sentinel(
    tmp_path,
):
    segment = Segment(5.0, 0.2, -0.3)
    filename = str(tmp_path / "actual-string.json")

    assert type(segment.to_json()) is str
    assert segment.to_json(str) == segment.to_json()
    assert segment.to_json(filename) is None
    assert Segment.from_json(filename) == segment


def test_json_source_and_target_type_errors_are_explicit():
    segment = Segment(1.0)

    with pytest.raises(TypeError, match="filename_or_url"):
        Segment.from_json(filename_or_url=object())
    with pytest.raises(TypeError, match="text"):
        Segment.from_json(text=object())
    with pytest.raises(TypeError, match="filename_or_url"):
        segment.to_json(None)
    with pytest.raises(TypeError, match="filename_or_url"):
        segment.to_json(object())


def test_non_http_url_like_string_is_a_local_path(monkeypatch, tmp_path):
    segment = Segment(6.0, -0.2, 0.4)
    monkeypatch.chdir(tmp_path)
    filename = Path("custom:/host/segment.json")
    filename.parent.mkdir(parents=True)
    filename.write_text(segment.to_json(str), encoding="utf-8")

    def unexpected_urlopen(*args: object, **kwargs: object) -> None:
        pytest.fail("a non-HTTP scheme must not be opened as a remote URL")

    monkeypatch.setattr(model.urllib.request, "urlopen", unexpected_urlopen)

    assert Segment.from_json("custom://host/segment.json") == segment


def test_json_http_read_detects_gzip_magic_without_extension(monkeypatch):
    segment = Segment(7.0, 0.1, 0.2)
    compressed = gzip.compress(segment.to_json(str, indent=None).encode(), mtime=0)

    def fake_urlopen(target: str | urllib.request.Request) -> _Response:
        assert target == "https://example.test/layout.bin"
        return _Response(compressed)

    monkeypatch.setattr(model.urllib.request, "urlopen", fake_urlopen)

    assert Segment.from_json("https://example.test/layout.bin") == segment
