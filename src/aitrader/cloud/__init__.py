"""Generation-guarded private object storage and restricted PAPER archives."""

import io
import json
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote, urlsplit

from aitrader.clock import Clock, WallClock


class CloudError(Exception):
    """Storage failed; messages never contain response bodies or credentials."""


class NotFoundError(CloudError):
    """The object does not exist."""


class PreconditionFailedError(CloudError):
    """Another writer changed or already created the object."""


NotFound = NotFoundError
PreconditionFailed = PreconditionFailedError


class StateStore(Protocol):
    def download(self, name: str) -> tuple[bytes, int]: ...

    def upload(self, name: str, data: bytes, if_generation_match: int) -> int: ...


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], data: bytes | None, timeout: float
) -> HttpResponse:
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HttpResponse(response.status, response.read(), dict(response.headers))
    except urllib.error.HTTPError as exc:
        with exc:
            return HttpResponse(exc.code, b"", {})
    except OSError, urllib.error.URLError:
        raise CloudError("storage transport failed") from None


def _check(response: HttpResponse) -> HttpResponse:
    if response.status == 404:
        raise NotFound("storage HTTP status 404")
    if response.status == 412:
        raise PreconditionFailed("storage HTTP status 412")
    if not 200 <= response.status < 300:
        raise CloudError(f"storage HTTP status {response.status}")
    return response


class MetadataTokenProvider:
    """Keep runtime identity only in memory and refresh before expiration."""

    def __init__(
        self,
        transport: Transport = urllib_transport,
        clock: Clock | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._transport = transport
        self._clock = clock or WallClock()
        self._timeout = timeout
        self._token = ""
        self._refresh: datetime | None = None

    def __call__(self) -> str:
        now = self._clock.now()
        if self._refresh is None or now >= self._refresh:
            response = _check(
                self._transport(
                    "GET",
                    "http://metadata.google.internal/computeMetadata/v1/instance/"
                    "service-accounts/default/token",
                    {"Metadata-Flavor": "Google"},
                    None,
                    self._timeout,
                )
            )
            try:
                payload = json.loads(response.body)
                token = payload["access_token"]
                expires = payload["expires_in"]
                if (
                    not isinstance(token, str)
                    or not token
                    or type(expires) is not int
                    or expires <= 0
                ):
                    raise ValueError
            except ValueError, KeyError, TypeError:
                raise CloudError("invalid metadata token response") from None
            self._token = token
            self._refresh = now + timedelta(seconds=expires - 60)
        return self._token


class GcsStateStore:
    def __init__(
        self,
        bucket: str,
        *,
        transport: Transport = urllib_transport,
        token_provider: Callable[[], str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._bucket = quote(bucket, safe="")
        self._transport = transport
        self._token = token_provider or MetadataTokenProvider(transport, timeout=timeout)
        self._timeout = timeout

    def _request(
        self, method: str, url: str, data: bytes | None, content_type: str
    ) -> HttpResponse:
        return _check(
            self._transport(
                method,
                url,
                {
                    "Authorization": f"Bearer {self._token()}",
                    "Content-Type": content_type,
                },
                data,
                self._timeout,
            )
        )

    def download(self, name: str) -> tuple[bytes, int]:
        response = self._request(
            "GET",
            f"https://storage.googleapis.com/storage/v1/b/"
            f"{self._bucket}/o/{quote(name, safe='')}?alt=media",
            None,
            "application/gzip",
        )
        headers = {key.lower(): value for key, value in response.headers.items()}
        return response.body, _generation(headers.get("x-goog-generation"))

    def upload(self, name: str, data: bytes, if_generation_match: int) -> int:
        content_type = (
            "application/json"
            if name.endswith(".json")
            else ("text/markdown" if name.endswith(".md") else "application/gzip")
        )
        response = self._request(
            "POST",
            f"https://storage.googleapis.com/upload/storage/v1/b/"
            f"{self._bucket}/o?uploadType=media&name={quote(name, safe='')}"
            f"&ifGenerationMatch={if_generation_match}",
            data,
            content_type,
        )
        try:
            payload = json.loads(response.body)
            value = payload["generation"]
        except ValueError, KeyError, TypeError:
            raise CloudError("invalid storage generation response") from None
        return _generation(value)


def _generation(value: object) -> int:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            result = int(value)
            if result > 0:
                return result
        except ValueError:
            pass
    raise CloudError("invalid storage generation")


def parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "gs"
        or not parsed.netloc
        or not parsed.path.strip("/")
        or parsed.query
        or parsed.fragment
        or "@" in parsed.netloc
        or ":" in parsed.netloc
    ):
        raise ValueError("expected gs://BUCKET/OBJECT")
    return parsed.netloc, parsed.path[1:]


DIRECTORIES = ("data/bars", "data/paper", "data/paper-s3")


def _allowed(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and any(
            path == PurePosixPath(root) or PurePosixPath(root) in path.parents
            for root in DIRECTORIES
        )
    )


def pack(work_dir: Path) -> bytes:
    """Snapshot only local regular files; links must never escape the book roots."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for root in DIRECTORIES:
            directory = work_dir / root
            if (work_dir / "data").is_symlink() or directory.is_symlink():
                raise CloudError("archive symlinks are forbidden")
            if directory.exists():
                for path in sorted((directory, *directory.rglob("*"))):
                    if path.is_symlink() or not (path.is_file() or path.is_dir()):
                        raise CloudError("archive requires regular files and directories")
                    archive.add(
                        path, arcname=path.relative_to(work_dir).as_posix(), recursive=False
                    )
    return buffer.getvalue()


def unpack(data: bytes, work_dir: Path) -> None:
    """Validate every member before extraction, including links already on disk."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not _allowed(member.name) or not (member.isfile() or member.isdir()):
                    raise CloudError("unsafe archive member")
                target = work_dir / member.name
                if any(path.is_symlink() for path in (target, *target.parents)):
                    raise CloudError("archive destination contains a symlink")
            archive.extractall(work_dir, filter="data")
    except tarfile.TarError, OSError:
        raise CloudError("invalid state archive") from None
