"""Single instance per mode and account (document 05, DUP-5): a file lock plus a lease row.

The file lock stops a second process on the same host; the lease row (with an expiry the
running engine renews) also covers a second host that shares the database through a mounted
volume, and shows who holds the engine.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import IO

from aitrader.storage.database import Database, StorageError


class InstanceLockedError(StorageError):
    """Another engine already runs for this mode and account."""


class SingleInstanceLock:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(f"{path}.lock")
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        handle = self.path.open("a+", encoding="utf-8")
        try:
            _lock(handle)
        except OSError as exc:
            handle.close()
            raise InstanceLockedError(f"{self.path} is held by another process") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is not None:
            _unlock(self._file)
            self._file.close()
            self._file = None

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


if sys.platform == "win32":  # pragma: no cover - the engine runs on Linux; this is for dev PCs
    import msvcrt

    def _lock(handle: IO[str]) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[str]) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_lease(db: Database, name: str, holder: str, now: datetime, ttl: timedelta) -> None:
    """Take or renew the lease ``name``; refused while another holder's lease is unexpired."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT holder, expires_at FROM leases WHERE name = ?", (name,)
        ).fetchone()
        held_by_other = row is not None and row["holder"] != holder
        if held_by_other and datetime.fromisoformat(row["expires_at"]) > now:
            raise InstanceLockedError(
                f"lease {name} is held by {row['holder']} until {row['expires_at']}"
            )
        conn.execute(
            "INSERT INTO leases (name, holder, expires_at) VALUES (?, ?, ?)"
            " ON CONFLICT (name) DO UPDATE SET holder = excluded.holder,"
            " expires_at = excluded.expires_at",
            (name, holder, (now + ttl).isoformat()),
        )


def release_lease(db: Database, name: str, holder: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM leases WHERE name = ? AND holder = ?", (name, holder))
