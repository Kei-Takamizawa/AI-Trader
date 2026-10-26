"""Private storage requests, token lifetimes and hostile archive inputs, entirely offline."""

import io
import json
import tarfile
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from aitrader import cloud
from aitrader.clock import SimClock


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, cloud.NotFound),
        (412, cloud.PreconditionFailed),
        (403, cloud.CloudError),
        (500, cloud.CloudError),
    ],
)
def test_status_errors(status: int, error: type[Exception]) -> None:
    def transport(*args: Any) -> cloud.HttpResponse:
        return cloud.HttpResponse(status, b"PRIVATE RESPONSE", {})

    store = cloud.GcsStateStore(
        "bucket", transport=transport, token_provider=lambda: "PRIVATE TOKEN"
    )
    with pytest.raises(error) as caught:
        store.download("state/a")
    assert str(status) in str(caught.value)
    assert "PRIVATE" not in str(caught.value)
    with pytest.raises(error):
        store.upload("state/a", b"archive", 7)


def test_requests_and_content_types() -> None:
    calls: list[tuple[Any, ...]] = []

    def transport(*args: Any) -> cloud.HttpResponse:
        calls.append(args)
        return cloud.HttpResponse(200, b'{"generation":"8"}', {"X-Goog-Generation": "7"})

    store = cloud.GcsStateStore("bucket", transport=transport, token_provider=lambda: "fake")
    assert store.download("state/a b")[1] == 7
    assert calls[-1][1].endswith("/state%2Fa%20b?alt=media")
    assert calls[-1][2]["Authorization"] == "Bearer fake"
    for name, content in [
        ("a.gz", "application/gzip"),
        ("a.json", "application/json"),
        ("a.md", "text/markdown"),
    ]:
        assert store.upload(name, b"payload", 7) == 8
        assert calls[-1][0] == "POST"
        assert "ifGenerationMatch=7" in calls[-1][1]
        assert calls[-1][2]["Content-Type"] == content
        assert calls[-1][3:] == (b"payload", 10.0)


@pytest.mark.parametrize("value", [None, True, [], 0, -1, "bad"])
def test_invalid_generation(value: object) -> None:
    with pytest.raises(cloud.CloudError, match="generation"):
        cloud._generation(value)


@pytest.mark.parametrize("body", [b"bad", b"[]", b"{}", b'{"generation":0}'])
def test_invalid_upload_response(body: bytes) -> None:
    store = cloud.GcsStateStore(
        "bucket",
        transport=lambda *args: cloud.HttpResponse(200, body, {}),
        token_provider=lambda: "fake",
    )
    with pytest.raises(cloud.CloudError):
        store.upload("a", b"b", 0)
    with pytest.raises(cloud.CloudError):
        store.download("a")


def test_token_refresh_and_default_provider() -> None:
    now = datetime(2024, 1, 2, tzinfo=UTC)
    clock = SimClock(now)
    calls = []

    def transport(
        method: str, url: str, headers: Mapping[str, str], data: bytes | None, timeout: float
    ) -> cloud.HttpResponse:
        calls.append(url)
        if "metadata.google.internal" in url:
            assert headers == {"Metadata-Flavor": "Google"}
            assert method == "GET"
            assert data is None
            assert timeout == 10.0
            return cloud.HttpResponse(200, b'{"access_token":"fake","expires_in":120}', {})
        return cloud.HttpResponse(200, b"x", {"x-goog-generation": "1"})

    token = cloud.MetadataTokenProvider(transport, clock)
    assert token() == "fake"
    clock.set(now + timedelta(seconds=59))
    assert token() == "fake"
    assert len(calls) == 1
    clock.set(now + timedelta(seconds=60))
    assert token() == "fake"
    assert len(calls) == 2
    assert cloud.GcsStateStore("bucket", transport=transport).download("a") == (b"x", 1)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [],
        {"access_token": 1, "expires_in": 120},
        {"access_token": "", "expires_in": 120},
        {"access_token": "fake", "expires_in": True},
        {"access_token": "fake", "expires_in": 0},
    ],
)
def test_invalid_token(payload: object) -> None:
    provider = cloud.MetadataTokenProvider(
        lambda *args: cloud.HttpResponse(200, json.dumps(payload).encode(), {})
    )
    with pytest.raises(cloud.CloudError, match="invalid metadata"):
        provider()


def test_transport_closes_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.status, response.read.return_value, response.headers = 200, b"ok", {}
    response.__enter__.return_value = response
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: response)
    assert cloud.urllib_transport("GET", "http://localhost/", {}, None, 1).body == b"ok"
    response.__exit__.assert_called_once()
    body = io.BytesIO(b"PRIVATE")
    error = urllib.error.HTTPError("http://localhost", 404, "missing", Message(), body)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert cloud.urllib_transport("GET", "http://localhost/", {}, None, 1).status == 404
    assert body.closed

    def offline(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.URLError("PRIVATE")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    with pytest.raises(cloud.CloudError, match=r"^storage transport failed$"):
        cloud.urllib_transport("GET", "http://localhost/", {}, None, 1)


@pytest.mark.parametrize(
    "uri",
    [
        "http://b/a",
        "gs:///a",
        "gs://b",
        "gs://b/",
        "gs://b/a?q=x",
        "gs://b/a#x",
        "gs://user@b/a",
        "gs://b:1/a",
    ],
)
def test_invalid_uri(uri: str) -> None:
    with pytest.raises(ValueError, match="gs://BUCKET/OBJECT"):
        cloud.parse_gs_uri(uri)


def test_archive_round_trip(tmp_path: Path) -> None:
    assert cloud.parse_gs_uri("gs://bucket/state/a") == ("bucket", "state/a")
    for name in cloud.DIRECTORIES:
        root = tmp_path / "input" / name
        root.mkdir(parents=True)
        (root / "file").write_bytes(b"all bytes\x00\xff\r\n")
    cloud.unpack(cloud.pack(tmp_path / "input"), tmp_path / "output")
    for name in cloud.DIRECTORIES:
        assert (tmp_path / "output" / name / "file").read_bytes() == b"all bytes\x00\xff\r\n"
    cloud.unpack(cloud.pack(tmp_path / "empty"), tmp_path / "empty-output")


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("../data/bars/a", tarfile.REGTYPE),
        ("/data/bars/a", tarfile.REGTYPE),
        ("config/a", tarfile.REGTYPE),
        ("data/bars/a", tarfile.SYMTYPE),
        ("data/bars/a", tarfile.LNKTYPE),
        ("data/bars/a", tarfile.FIFOTYPE),
        ("data/bars\\a", tarfile.REGTYPE),
    ],
)
def test_hostile_archive(tmp_path: Path, name: str, kind: bytes) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.type, member.linkname = kind, "/outside"
        archive.addfile(member)
    with pytest.raises(cloud.CloudError, match="unsafe"):
        cloud.unpack(buffer.getvalue(), tmp_path)
    assert not list(tmp_path.iterdir())


def test_archive_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(cloud.CloudError, match="invalid state"):
        cloud.unpack(b"invalid", tmp_path)
    root = tmp_path / "data/bars"
    root.mkdir(parents=True)
    data = cloud.pack(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == root or original(p))
    with pytest.raises(cloud.CloudError, match="symlink"):
        cloud.pack(tmp_path)
    with pytest.raises(cloud.CloudError, match="symlink"):
        cloud.unpack(data, tmp_path)
    monkeypatch.setattr(Path, "is_symlink", original)
    (root / "file").write_bytes(b"x")
    monkeypatch.setattr(Path, "is_file", lambda p: False)
    with pytest.raises(cloud.CloudError, match="regular"):
        cloud.pack(tmp_path)
